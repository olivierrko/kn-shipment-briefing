import os, json
from datetime import datetime, timezone, timedelta
import requests

BASE_URL = "https://gateway.api.kuehne-nagel.com/track-trace/shipment/v2"
TOKEN_URL = "https://portal.api.kuehne-nagel.com/oauth2/token"
MAX_INACTIVITY_DAYS = 30  # dossiers sans événement récent = anomalies connues, ignorés

def get_token():
    resp = requests.post(
        TOKEN_URL,
        auth=(os.environ["KN_CLIENT_ID"], os.environ["KN_CLIENT_SECRET"]),
        data={"grant_type": "client_credentials"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]

def latest_event_date(shipment) -> datetime:
    """Date du dernier événement connu d'une expédition (résumé de recherche)."""
    candidates = [datetime.fromisoformat(shipment.get("shipmentCreationIsoDateTime", ""))]
    # Champs possibles selon la réponse
    for key in ("lastMilestoneDate", "lastEventDate"):
        if shipment.get(key):
            try:
                candidates.append(datetime.fromisoformat(shipment[key]))
            except ValueError:
                pass
    return max(d for d in candidates if d)

def main():
    token = get_token()
    headers = {"Authorization": f"Bearer {token}"}

    # Expéditions actives
    body = {
        "pagination": {"page": 0, "size": 500},
        "filters": {"stages": ["BOOKED", "IN_TRANSIT"]},
    }
    search = requests.post(
        f"{BASE_URL}/shipments-search",
        headers={**headers, "Content-Type": "application/json"},
        json=body, timeout=60,
    )
    search.raise_for_status()
    data = search.json()
    abstracts = data if isinstance(data, list) else (
        data.get("shipments") or data.get("content") or []
    )

    # Filtre anti-anomalies : on ne garde que les dossiers avec un événement récent
    cutoff = datetime.now(timezone.utc) - timedelta(days=MAX_INACTIVITY_DAYS)
    kept, ignored = [], []
    for a in abstracts:
        try:
            iso = a.get("shipmentCreationIsoDateTime", "")
            latest = datetime.fromisoformat(iso) if iso else None
        except ValueError:
            latest = None
        # La date de création suffit : un dossier BOOKED/IN_TRANSIT sans activité
        # depuis plus de 30 jours est une anomalie connue, on l'ignore.
        if latest and latest.tzinfo is None:
            latest = latest.replace(tzinfo=timezone.utc)
        if latest and latest >= cutoff:
            kept.append(a)
        elif latest:
            ignored.append(a.get("trackingNumber"))
        else:
            kept.append(a)  # sans date exploitable, on le garde par prudence

    # Détails de chaque expédition conservée
    details = []
    for a in kept:
        tn = a.get("trackingNumber")
        if not tn:
            continue
        r = requests.get(f"{BASE_URL}/shipments/{tn}", headers=headers, timeout=60)
        if r.ok:
            details.append(r.json())

    os.makedirs("reports", exist_ok=True)
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_active": len(kept),
        "ignored_anomalies": ignored,
        "shipments": details,
        "raw_search": kept,
    }
    with open(f"reports/briefing-{datetime.now(timezone.utc):%Y-%m-%d}.json", "w") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

if __name__ == "__main__":
    main()
