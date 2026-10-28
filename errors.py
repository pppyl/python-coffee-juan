class ProviderError(Exception):
    def __init__(self, message, status=409, code="provider_error", **details):
        super().__init__(message)
        self.message, self.status, self.code, self.details = message, status, code, details
