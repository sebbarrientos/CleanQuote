import os
import json
import math
from datetime import datetime, date, time, timedelta
from flask import Flask, render_template, request, jsonify, redirect, url_for, session, make_response

try:
    from openai import OpenAI
    openai_client = OpenAI()
except Exception:
    openai_client = None

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret")

ROOT_DIR = os.path.dirname(__file__)
PRICES_PATH = os.path.join(ROOT_DIR, 'prices.json')

with open(PRICES_PATH, 'r') as f:
    PRICES = json.load(f)

ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD')
FROM_EMAIL = os.environ.get('FROM_EMAIL', os.environ.get('REPLY_TO', ''))
TO_FALLBACK = os.environ.get('TO_FALLBACK', '')
RESEND_API_KEY = os.environ.get('RESEND_API_KEY', '')

def money(x):
    return round(float(x or 0), 2)

def apply_discount(base, pct):
    return money(base * (1 - pct))

UK_BANK_HOLIDAYS = set()

def is_closed(d: date):
    return d.weekday() == 6 or d in UK_BANK_HOLIDAYS

def compute_price(payload):
    svc = payload.get('service')
    total = 0
    breakdown = []

    if svc == 'eot':
        size = payload.get('size')
        base = PRICES['end_of_tenancy'].get(size, 0)
        breakdown.append(["End of Tenancy (" + size.replace('_', ' ').title() + ")", base])
        total += base

    elif svc == 'airbnb':
        size = payload.get('size')
        base = PRICES['airbnb_turnover'].get(size, 0)
        breakdown.append(["Airbnb Turnover (" + size.replace('_', ' ').title() + ")", base])
        total += base

    elif svc == 'communal':
        block_size = payload.get('block_size')
        freq = payload.get('frequency', 'monthly')
        base = PRICES['communal']['base'].get(block_size, 0)
        discount = PRICES['communal']['frequency_discounts'].get(freq, 0)
        line = f"Communal clean ({block_size.title()}, {freq.title()})"
        price = apply_discount(base, discount)
        breakdown.append([line, price])
        total += price
        if payload.get('lift_count'):
            lifts = int(payload['lift_count'])
            if lifts > 0:
                extra = PRICES['communal']['extras']['lift'] * lifts
                breakdown.append([f"Lift cleaning ×{lifts}", extra])
                total += extra
        if payload.get('bin_store') == 'yes':
            extra = PRICES['communal']['extras']['bin_store']
            breakdown.append(["Bin store cleaning", extra])
            total += extra

    elif svc == 'general':
        recurring = payload.get('recurring', 'no')
        base = PRICES['general_clean']['one_off_min']
        label = "General clean (one-off)"
        if recurring in ['weekly', 'biweekly', 'monthly']:
            pct = PRICES['general_clean']['recurring_discounts'][recurring]
            base = apply_discount(base, pct)
            label = f"General clean ({recurring})"
        breakdown.append([label, base])
        total += base

    elif svc == 'carpet':
        rooms = int(payload.get('rooms', 0))
        lounge = int(payload.get('lounges', 0))
        bedrooms = int(payload.get('bedrooms', 0))
        hall = int(payload.get('landing_hall', 0))
        steps = int(payload.get('stairs_steps', 0))
        flights = int(payload.get('stairs_flights', 0))
        rugs_s = int(payload.get('rugs_small', 0))
        rugs_l = int(payload.get('rugs_large', 0))
        p = PRICES['carpet']
        if rooms:
            breakdown.append([f"Carpet: Room ×{rooms}", p['room'] * rooms])
            total += p['room'] * rooms
        if lounge:
            breakdown.append([f"Carpet: Lounge ×{lounge}", p['lounge'] * lounge])
            total += p['lounge'] * lounge
        if bedrooms:
            breakdown.append([f"Carpet: Bedroom ×{bedrooms}", p['bedroom'] * bedrooms])
            total += p['bedroom'] * bedrooms
        if hall:
            breakdown.append([f"Carpet: Landing/Hall ×{hall}", p['landing_hall'] * hall])
            total += p['landing_hall'] * hall
        if steps:
            breakdown.append([f"Carpet: Stairs per step ×{steps}", p['stairs_per_step'] * steps])
            total += p['stairs_per_step'] * steps
        if flights:
            breakdown.append([f"Carpet: Standard flight ×{flights}", p['stairs_flat'] * flights])
            total += p['stairs_flat'] * flights
        if rugs_s:
            breakdown.append([f"Carpet: Rug (small) ×{rugs_s}", p['rug_small'] * rugs_s])
            total += p['rug_small'] * rugs_s
        if rugs_l:
            breakdown.append([f"Carpet: Rug (large) ×{rugs_l}", p['rug_large'] * rugs_l])
            total += p['rug_large'] * rugs_l

    if payload.get('pets') == 'yes':
        breakdown.append(["Pet surcharge", PRICES['surcharges']['pets']])
        total += PRICES['surcharges']['pets']
    if payload.get('urgent') == 'yes':
        breakdown.append(["Same-day surcharge", PRICES['surcharges']['urgent_same_day']])
        total += PRICES['surcharges']['urgent_same_day']
    if payload.get('congestion') == 'yes':
        breakdown.append(["Congestion Charge", PRICES['surcharges']['congestion']])
        total += PRICES['surcharges']['congestion']
    if payload.get('parking') == 'yes':
        breakdown.append(["Parking (flat)", PRICES['surcharges']['parking_flat']])
        total += PRICES['surcharges']['parking_flat']

    code = (payload.get('promo') or '').upper().strip()
    if code and code in PRICES.get('promo_codes', {}):
        pcode = PRICES['promo_codes'][code]
        if pcode.get('active'):
            pct = pcode.get('percent', 0)/100.0
            discount_amount = money(total * pct)
            breakdown.append([f"Promo {code}", -discount_amount])
            total -= discount_amount

    if total < PRICES['min_charge']:
        topup = PRICES['min_charge'] - total
        breakdown.append(["Minimum charge top-up", topup])
        total = PRICES['min_charge']

    vat = PRICES['vat']
    if vat:
        vat_amount = money(total * vat)
        breakdown.append([f"VAT @ {int(vat*100)}%", vat_amount])
        total += vat_amount

    return {"total": money(total), "breakdown": [[k, money(v)] for k, v in breakdown]}

def send_quote_email(to_email: str, subject: str, html: str):
    if not RESEND_API_KEY or not to_email:
        return False, "Email disabled or missing address"
    try:
        import requests
        payload = {
            "from": FROM_EMAIL or "quotes@albaandco.example",
            "to": [to_email],
            "bcc": [TO_FALLBACK] if TO_FALLBACK else [],
            "subject": subject,
            "html": html
        }
        r = requests.post(
            'https://api.resend.com/emails',
            headers={"Authorization": f"Bearer {RESEND_API_KEY}", "Content-Type": "application/json"},
            json=payload,
            timeout=15
        )
        ok = r.status_code in (200, 202)
        return ok, r.text
    except Exception as e:
        return False, str(e)

@app.route('/')
def index():
    return render_template('index.html')

@app.post('/quote/preview')
def quote_preview():
    data = request.get_json(force=True)
    result = compute_price(data)
    return jsonify(result)

@app.post('/book')
def book():
    form = request.form.to_dict()
    price = compute_price(form)
    html = render_template('result.html',
                           brand_name='Alba & Co Services',
                           contact=form,
                           summary=price,
                           disclaimer=get_footer_disclaimer())
    ok_client, _ = send_quote_email(form.get('email'), 'Your cleaning quote', html)
    if TO_FALLBACK:
        send_quote_email(TO_FALLBACK, 'New quote submission', html)

    quotes = session.get('quotes', [])
    quotes.append({
        'created_at': datetime.utcnow().isoformat(),
        'service': form.get('service'),
        'total': price['total'],
        'postcode': form.get('postcode', ''),
        'contact_name': form.get('name', ''),
        'email': form.get('email', ''),
        'phone': form.get('phone', ''),
        'slot': f"{form.get('date')} {form.get('slot')}",
        'status': 'Sent' if ok_client else 'New'
    })
    session['quotes'] = quotes

    return render_template('result.html',
                           brand_name='Alba & Co Services',
                           contact=form,
                           summary=price,
                           disclaimer=get_footer_disclaimer())

@app.route('/admin', methods=['GET', 'POST'])
def admin():
    if request.method == 'POST':
        if request.form.get('password') == ADMIN_PASSWORD:
            session['admin_ok'] = True
            return redirect(url_for('admin'))
    if not session.get('admin_ok'):
        return render_template('admin_login.html')

    q = request.args.get('q', '').lower()
    items = session.get('quotes', [])
    if q:
        items = [x for x in items if q in json.dumps(x).lower()]
    return render_template('admin_list.html', items=items, q=q)

def get_footer_disclaimer():
    return (
        "Quotes are based on standard access and condition. Heavy soiling, pest activity, "
        "restricted access, parking/permit fees, or additional requests may affect final price. "
        "End-of-Tenancy excludes maintenance/repairs. Carpet results vary by fibre, age, and prior treatments."
    )

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
