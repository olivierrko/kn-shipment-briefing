import os, json
from datetime import datetime, timezone, timedelta
import requests

BASE_URL = "https://gateway.api.kuehne-nagel.com/track-trace/shipment/v2"
TOKEN_URL = "https://portal.api.kuehne-nagel.com/oauth2/token"
MAX_INACTIVITY_DAYS = 30   # dossiers actifs sans événement récent = anomalies connues, ignorés
DELIVERED_LOOKBACK_DAYS = 90  # fenêtre d'analyse des délais de livraison

def get_token():
    resp = requests.post(
        TOKEN_URL,
        auth=(os.environ["KN_CLIENT_ID"], os.environ["KN_CLIENT_SECRET"]),
        data={"grant_type": "client_credentials"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]

def fetch_details(token, tracking_numbers):
    """Récupère les détails de chaque expédition (avec tolérance d'erreur)."""
    headers = {"Authorization": f"Bearer {token}"}
    details = []
    for tn in tracking_numbers:
        try:
            r = requests.get(f"{BASE_URL}/shipments/{tn}", headers=headers, timeout=60)
            if r.ok:
                details.append(r.json())
        except requests.RequestException:
            continue
    return details

def find_date(ms, milestone_type):
    """Cherche la date d'un jalon donné dans milestoneInfo."""
    for m in ms.get("milestoneDates", []) or []:
        key = m.get("key", {})
        if key.get("locationMilestoneType") == milestone_type:
            d = (m.get("actualAchievementDateTime") or {}).get("dateTime")
            if d:
                return d
    return None

def parse_dt(s):
    if not s:
        return None
    # Formats: 2026-10-09T20:07+02:00 ou 2026-10-09T20:07:40Z
    s = s.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None

def compute_lead_times(details):
    """Calcule le délai (création → livraison) de chaque expédition livrée."""
    results = []
    for s in details:
        created = parse_dt(s.get("shipmentCreationIsoDateTime"))
        ms = s.get("routing", {}).get("milestoneInfo", {})
        delivered_str = find_date(ms, "CARGO_DELIVERED")
        picked_str = find_date(ms, "CARGO_PICKED_UP")
        delivered = parse_dt(delivered_str)
        picked = parse_dt(picked_str)
        if not created or not delivered:
            continue
        lead_total_h = (delivered - created).total_seconds() / 3600
        lead_transport_h = (delivered - picked).total_seconds() / 3600 if picked else None
        results.append({
            "trackingNumber": s.get("trackingNumber"),
            "created": created.isoformat(),
            "delivered": delivered.isoformat(),
            "lead_time_total_hours": round(lead_total_h, 1),
            "lead_time_transport_hours": round(lead_transport_h, 1) if lead_transport_h is not None else None,
        })
    return results

def bucketize(lead_times):
    """Répartit en classes 24h / 48h / 72h / hors délais."""
    total = len(lead_times)
    if total == 0:
        return {"total": 0}
    b = {"within_24h": 0, "24_48h": 0, "48_72h": 0, "over_72h": 0}
    for lt in lead_times:
        h = lt["lead_time_total_hours"]
        if h <= 24: b["within_24h"] += 1
        elif h <= 48: b["24_48h"] += 1
        elif h <= 72: b["48_72h"] += 1
        else: b["over_72h"] += 1
    pct = {k: round(100 * v / total, 1) for k, v in b.items()}
    return {"total": total, **b, "percentages": pct}

def main():
    token = get_token()
    headers = {"Authorization": f"Bearer {token}"}

    # --- Expéditions actives (briefing quotidien) ---
    body_active = {
        "pagination": {"page": 0, "size": 500},
        "filters": {"stages": ["BOOKED", "IN_TRANSIT"]},
    }
    search = requests.post(
        f"{BASE_URL}/shipments-search",
        headers={**headers, "Content-Type": "application/json"},
        json=body_active, timeout=60,
    )
    search.raise_for_status()
    data = search.json()
    abstracts = data if isinstance(data, list) else (
        data.get("shipments") or data.get("content") or []
    )

    # Filtre anti-anomalies sur les actifs
    cutoff = datetime.now(timezone.utc) - timedelta(days=MAX_INACTIVITY_DAYS)
    kept, ignored = [], []
    for a in abstracts:
        iso = a.get("shipmentCreationIsoDateTime", "")
        try:
            latest = datetime.fromisoformat(iso) if iso else None
        except ValueError:
            latest = None
        if latest and latest.tzinfo is None:
            latest = latest.replace(tzinfo=timezone.utc)
        if latest and latest >= cutoff:
            kept.append(a)
        elif latest:
            ignored.append(a.get("trackingNumber"))
        else:
            kept.append(a)

    active_details = fetch_details(token, [a["trackingNumber"] for a in kept if a.get("trackingNumber")])

    # --- Expéditions livrées (analyse des délais) ---
    # On cherche les livrées via des jalons de livraison sur la fenêtre glissante
    body_delivered = {
        "pagination": {"page": 0, "size": 500},
        "filters": {
            "stages": ["COMPLETED"],
        },
    }
    delivered_search = requests.post(
        f"{BASE_URL}/shipments-search",
        headers={**headers, "Content-Type": "application/json"},
        json=body_delivered, timeout=60,
    )
    delivered_abstracts = []
    if delivered_search.ok:
        d = delivered_search.json()
        delivered_abstracts = d if isinstance(d, list) else (
            d.get("shipments") or d.get("content") or []
        )
    # Filtre sur la fenêtre de 90 jours (par date de création pour rester large)
    delivered_cutoff = datetime.now(timezone.utc) - timedelta(days=DELIVERED_LOOKBACK_DAYS)
    delivered_kept = []
    for a in delivered_abstracts:
        iso = a.get("shipmentCreationIsoDateTime", "")
        try:
            c = datetime.fromisoformat(iso) if iso else None
        except ValueError:
            c = None
        if c:
            if c.tzinfo is None:
                c = c.replace(tzinfo=timezone.utc)
            if c >= delivered_cutoff:
                delivered_kept.append(a)

    delivered_details = fetch_details(token, [a["trackingNumber"] for a in delivered_kept if a.get("trackingNumber")])

    # Calcul des délais
    lead_times = compute_lead_times(delivered_details)
    buckets = bucketize(lead_times)

    # --- Rapport ---
    os.makedirs("reports", exist_ok=True)
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "active": {
            "total_active": len(kept),
            "ignored_anomalies": ignored,
            "shipments": active_details,
            "raw_search": kept,
        },
        "delivery_performance": {
            "window_days": DELIVERED_LOOKBACK_DAYS,
            "total_delivered": len(lead_times),
            "lead_times": lead_times,
            "buckets": buckets,
        },
    }
    with open(f"reports/briefing-{datetime.now(timezone.utc):%Y-%m-%d}.json", "w") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

if __name__ == "__main__":
    main()
