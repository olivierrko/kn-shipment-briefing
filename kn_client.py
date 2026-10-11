import requests


class KNClient:
    """Client myKN Track Shipment Details API v2 (OAuth2 client credentials)."""

    BASE_URL = "https://gateway.api.kuehne-nagel.com/track-trace/shipment/v2"

    def __init__(self, client_id: str, client_secret: str):
        self.client_id = client_id
        self.client_secret = client_secret
        self._token = None

    def _get_token(self) -> str:
        if self._token:
            return self._token
        resp = requests.post(
            self.BASE_URL + "/oauth/token",
            auth=(self.client_id, self.client_secret),
            data={"grant_type": "client_credentials"},
            timeout=30,
        )
        resp.raise_for_status()
        self._token = resp.json()["access_token"]
        return self._token

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._get_token()}"}

    def _get(self, path: str) -> dict:
        resp = requests.get(self.BASE_URL + path, headers=self._headers(), timeout=60)
        resp.raise_for_status()
        return resp.json()

    def _post(self, path: str, body: dict) -> dict:
        resp = requests.post(
            self.BASE_URL + path,
            headers={**self._headers(), "Content-Type": "application/json"},
            json=body,
            timeout=60,
        )
        resp.raise_for_status()
        return resp.json()

    def search_shipments(self, stages=None, modes=None, reference=None, page=0, size=100) -> dict:
        """POST /shipments-search — résumés d'expéditions selon filtres."""
        body = {"pagination": {"page": page, "size": size}}
        filters = {}
        if stages:
            filters["stages"] = stages
        if modes:
            filters["modesOfTransport"] = modes
        if reference:
            filters["reference"] = reference
        if filters:
            body["filters"] = filters
        return self._post("/shipments-search", body)

    def get_shipment_details(self, tracking_number: str) -> dict:
        """GET /shipments/{tracking-number} — détail complet."""
        return self._get(f"/shipments/{tracking_number}")

    def get_milestone_history(self, shipment_id: str) -> dict:
        """GET /shipments/{shipment-id}/history-records — historique des jalons."""
        return self._get(f"/shipments/{shipment_id}/history-records")
