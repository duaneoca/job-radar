"""
Gmail API integration — Phase 4
"""


class GmailMonitor:
    """
    Monitors Gmail inbox for employer responses.
    Phase 4: implement OAuth2 flow and message polling.
    """

    def __init__(self, credentials_file: str):
        self.credentials_file = credentials_file

    def get_messages_from_company(self, company_domain: str) -> list:
        # TODO (Phase 4): use google-auth + gmail API
        raise NotImplementedError("Gmail monitor coming in Phase 4")
