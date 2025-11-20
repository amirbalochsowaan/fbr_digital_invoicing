import frappe
from erpnext.accounts.doctype.sales_invoice.sales_invoice import SalesInvoice as SalesInvoiceController
from fbr_digital_invoicing.api import FBRDigitalInvoicingAPI  
from frappe.utils import cint
import pyqrcode
import re
    


class SalesInvoice(SalesInvoiceController):
    def before_cancel(self):
        if self.custom_fbr_invoice_no:
            frappe.throw("Cannot cancel a Sales Invoice that has been posted to FBR.")
        
        super().before_cancel()
        
    def on_submit(self):
        super().on_submit()
        if not self.custom_post_to_fdi:
            return
        if len(self.taxes) == 0:
            return

        if not frappe.db.exists("FBR Digital Invoicing Setting", self.company):
            frappe.throw("FBR Digital Invoicing Settings not found for company: {}".format(self.company))

        settings = frappe.get_doc("FBR Digital Invoicing Setting", self.company)
        data = self.get_mapped_data(settings)
        api_log = frappe.new_doc("FDI Request Log")
        api_log.request_data = frappe.as_json(data, indent=4)

        frappe.log_error(title="FBR Request Data", message=str(data))


        try:
            api = FBRDigitalInvoicingAPI(self.company)
            response = api.make_request("POST", settings.get("invoice_post_method"), data)
            resdata = response.get("validationResponse")
            
            if resdata.get("status") == "Valid":
                fbr_invoice_num = response.get("invoiceNumber")
                url = pyqrcode.create(fbr_invoice_num)
                url.svg(frappe.get_site_path()+'/public/files/'+self.name+'_online_qrcode.svg', scale=8)
                
                frappe.db.set_value("Sales Invoice", self.name, 
                                    {
                                        "custom_fbr_invoice_no": fbr_invoice_num,
                                        "custom_qr_code": '/files/'+self.name+'_online_qrcode.svg'
                                    })
                
                api_log.response_data = frappe.as_json(response, indent=4)
                api_log.save()
                frappe.msgprint("Invoice successfully submitted to FBR Digital Invoicing.")
            else:
                api_log.response_data = frappe.as_json(response, indent=4)
                api_log.save()
                frappe.throw(
                    f"Error in FBR Digital Invoicing: {resdata.get('error')}"
                )
                  
                
        except Exception as e:
            frappe.db.set_value("Sales Invoice", self.name, "custom_post_to_fdi", 0)
            api_log.error = frappe.as_json(e, indent=4)
            api_log.save()
            frappe.db.commit()

            frappe.log_error(
                title="FBR Digital Invoicing API Error",
                message=frappe.get_traceback()
            )
            
            frappe.throw(f"Error while submitting invoice to FBR: {str(e)}")

        api_log.save()
    def get_mapped_data(self, settings):
        company = frappe.get_doc("Company", self.company)
        customer = frappe.get_doc("Customer", self.customer)
        if not customer.customer_primary_address:
            frappe.throw("Customer does not have a primary address.")
        customer_address = frappe.get_doc("Address", customer.customer_primary_address)
        if not customer_address.state:
            frappe.throw("Customer primary address does not have a State/Province.")

        data = {}
        data["invoiceType"] = "Debit Note" if self.is_return else "Sale Invoice"
        
        if self.is_return:
            ref_no = frappe.db.get_value("Sales Invoice",self.return_against, "custom_fbr_invoice_no")
            data["invoiceRefNo"] = ref_no+""
            data["reason"] = self.get("custom_return_reason")
            if self.get("custom_return_reason") == "Others":
                data["reasonRemarks"] = self.get("custom_other_reason")

        data["invoiceDate"] = str(self.posting_date)
        
        data["sellerNTNCNIC"] = company.tax_id
        data["sellerBusinessName"] = self.company
        data["sellerProvince"] = company.custom_province if company.custom_province else "Sindh"
        # Uncomment the next line if you have a seller address field
        sellerAdress = self.company_address_display if self.company_address_display else ""
        data["sellerAddress"] = self.normalize_address(sellerAdress)


        data["buyerNTNCNIC"] = customer.tax_id if customer.tax_id else ""
        data["buyerBusinessName"] = self.customer_name
        data["buyerProvince"] = customer_address.state
        buyerAddress = customer.primary_address if customer.primary_address else ""
        data["buyerAddress"] = self.normalize_address(buyerAddress)
        data["buyerRegistrationType"] = "Unregistered" if not customer.tax_id else "Registered"
        
        if settings.environment == "Sandbox":
            if not self.custom_sn_id:
                data["scenarioId"] = "SN002" if not customer.tax_id else "SN001"  # Adjust based on your logic
            else:
                data["scenarioId"] = self.custom_sn_id

        data["items"] = self.get_items()

        frappe.log_error(title="FBR Data", message=str(data))        
        return data
    
    def get_items(self):
        items = []
        for item in self.items:
            if not item.custom_hs_code:
                item.custom_hs_code = self.get_and_set_hs_code(item)
                
            uom = self.get_and_set_uom(item.custom_hs_code)

            item_data = {
                "hsCode": item.custom_hs_code,  # Default HS Code if not set
                "productDescription": item.item_code,
                "rate": f"{cint(self.taxes[0].rate)}%",
                "uoM": uom,
                "quantity": abs(item.qty),
                "totalValues": 0,  # Placeholder, adjust as needed
                "valueSalesExcludingST": abs(item.amount),
                "fixedNotifiedValueOrRetailPrice": 0,  # Placeholder, adjust as needed
                "salesTaxApplicable": round(abs(item.amount) * self.taxes[0].rate /100, 2) if self.taxes else 0,  # Assuming first tax is sales tax
                "salesTaxWithheldAtSource": 0,  # Placeholder, adjust as needed
                "extraTax": "",  # Placeholder, adjust as needed
                "furtherTax": 0,  # Assuming first tax is further tax
                "sroScheduleNo": "",  # Placeholder, adjust as needed
                "fedPayable": 0,  # Placeholder, adjust as needed
                "discount": cint(item.discount_amount) or 0,
                "saleType": "Goods at standard rate (default)",  # Adjust based on your logic
                "sroItemSerialNo": ""  # Placeholder, adjust as needed
            }
            items.append(item_data)
        return items

    def get_and_set_uom(self, hs_code):
        hs_code_doc = frappe.new_doc("HS Code")
        if frappe.db.exists("HS Code", hs_code):
            hs_code_doc = frappe.get_doc("HS Code", hs_code)

        api = FBRDigitalInvoicingAPI(self.company)
        response = api.make_request("GET", f"/pdi/v2/HS_UOM?hs_code={hs_code}&annexure_id=3")
        if response:
            #res = response.json()
            uom = response[0].get("description")
            hs_code_doc.hs_code = hs_code
            hs_code_doc.uom = uom
            hs_code_doc.save()
            return uom

    def get_and_set_hs_code(self, item):
        item_hs_code = frappe.db.get_value("Item", item.item_code, "custom_hs_code")
        if not item_hs_code:
            frappe.throw("HS Code is missing for item: {}".format(item.item_code))

        return item_hs_code

    def normalize_address(self, address):
        cleaned = re.sub(r'<br>\s*', ' ', address)
        cleaned = cleaned.replace("\n", " ")

        # Remove extra spaces
        normalized = re.sub(r'\s+', ' ', cleaned).strip()
        return normalized

@frappe.whitelist()
def post_to_fbr(docname, sn_id=None):
    doc = frappe.get_doc("Sales Invoice", docname)
    if not frappe.db.exists("FBR Digital Invoicing Setting", doc.company):
        frappe.throw("FBR Digital Invoicing Settings not found for company: {}".format(doc.company))
        
    if doc.custom_post_to_fdi:
        frappe.throw("Already synced to FDI.")
    if doc.docstatus != 1:
        frappe.throw("Invoice is not submitted yet, please submit the invoice and try again.")

    if len(doc.taxes) == 0:
            frappe.throw("No taxes found to submit to FBR.")
        

    frappe.db.set_value("Sales Invoice", doc.name, "custom_post_to_fdi", 1)
    if sn_id:
        frappe.db.set_value("Sales Invoice", doc.name, "custom_sn_id", sn_id)

    doc = frappe.get_doc("Sales Invoice", docname)
    doc.run_method("on_submit")

    frappe.db.commit()

        
        