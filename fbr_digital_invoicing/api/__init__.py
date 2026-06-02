import frappe
import requests



class FBRDigitalInvoicingAPI:
    def __init__(self, company: str):
        if not company:
            frappe.throw("Company is required to initialize FBR Digital Invoicing API")

        settings = frappe.get_doc("FBR Digital Invoicing Setting", company)
        self.base_url = settings.get("base_url")
        self.token = settings.get("token")

    def init_request(self):
        self.headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.token}"
        }
        self.session = requests.Session()
        self.session.headers.update(self.headers)


    def make_request(self, method, endpint, data=None):
        self.init_request()
        # Remove double slashes in URL
        endpoint = endpint.lstrip('/') if endpint.startswith('/') else endpint
        full_url = f"{self.base_url}/{endpoint}"

        frappe.log_error(
            title="FBR API Request",
            message=f"URL: {full_url}\nMethod: {method}\nData: {frappe.as_json(data, indent=2) if data else 'None'}"
        )

        request = self.session.request(method, full_url, json=data)

        if request.status_code != 200:
            error_detail = self._parse_error_response(request)

            log_msg = f"Status: {request.status_code}\n"
            log_msg += f"URL: {full_url}\n"
            log_msg += f"Response: {error_detail}"

            frappe.log_error(
                title="FBR Digital Invoicing API Error",
                message=log_msg
            )

            frappe.throw(f"FBR API Error ({request.status_code}): {error_detail}")

        try:
            return request.json()
        except Exception:
            frappe.log_error(
                title="FBR Invalid JSON Response",
                message=f"Status: {request.status_code}\nResponse: {request.text}"
            )
            return {
                "validationResponse": {
                    "status": "Invalid",
                    "error": request.text
                }
            }

    def _parse_error_response(self, response):
        try:
            data = response.json()
            if isinstance(data, dict):
                if "fault" in data:
                    fault = data["fault"]
                    return f"{fault.get('message', 'Unknown')} - {fault.get('description', '')}"
                elif "error" in data:
                    return str(data["error"])
                else:
                    return response.text
            return response.text
        except:
            return response.text
    

