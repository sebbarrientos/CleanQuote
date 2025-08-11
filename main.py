import json
from flask import Flask, render_template, request
from openai import OpenAI
from markdown import markdown

app = Flask(__name__)
client = OpenAI()

# Load prices at startup
with open("prices.json", "r") as f:
    PRICES = json.load(f)

MIN_CHARGE = 50
PETS_SURCHARGE = 30
URGENT_SURCHARGE = 50

def compute_total(cleaning_type, property_type, bedrooms, bathrooms, wc, pets_flag, urgent_flag, travel_flag):
    breakdown = []
    total = 0.0
    note_lines = []

    ct = cleaning_type  # shorter alias

    if ct == "end_of_tenancy":
        # base
        if property_type == "studio":
            base = PRICES["end_of_tenancy"]["studio"]
        else:
            table = PRICES["end_of_tenancy"][property_type]
            base = table.get(str(bedrooms), 0)

        total += base
        breakdown.append(f"End of Tenancy base ({property_type}, {bedrooms} bed): £{base}")
        breakdown.append("First bathroom included")

        # extras
        if bathrooms and bathrooms > 1:
            extra_bath = (bathrooms - 1) * PRICES["end_of_tenancy"]["extra_bathroom"]
            total += extra_bath
            breakdown.append(f"Extra bathrooms ({bathrooms-1} × £{PRICES['end_of_tenancy']['extra_bathroom']}): £{extra_bath}")

        if wc and wc > 0:
            wc_cost = wc * PRICES["end_of_tenancy"]["extra_wc"]
            total += wc_cost
            breakdown.append(f"Extra WC ({wc} × £{PRICES['end_of_tenancy']['extra_wc']}): £{wc_cost}")

    elif ct == "airbnb":
        # base
        if property_type == "studio":
            base = PRICES["airbnb"]["studio"]
        else:
            table = PRICES["airbnb"][property_type]
            base = table.get(str(bedrooms), 0)

        total += base
        breakdown.append(f"Airbnb turnover base ({property_type}, {bedrooms} bed): £{base}")

        # extras
        if bathrooms and bathrooms > 1:
            extra_bath = (bathrooms - 1) * PRICES["airbnb"]["extra_bathroom"]
            total += extra_bath
            breakdown.append(f"Extra bathrooms ({bathrooms-1} × £{PRICES['airbnb']['extra_bathroom']}): £{extra_bath}")

    else:
        # For now, show minimum charge and ask to confirm pricing
        breakdown.append(ct.replace("_", " ").title() + " – pricing to be confirmed")
        note_lines.append("We’ll confirm the exact price based on size, access and frequency.")
        total += 0  # will be raised to minimum below

    # Surcharges / notes
    if pets_flag:
        total += PETS_SURCHARGE
        breakdown.append(f"Pets present: £{PETS_SURCHARGE}")

    if urgent_flag:
        total += URGENT_SURCHARGE
        breakdown.append(f"Urgent booking (within 48hrs): £{URGENT_SURCHARGE}")

    if travel_flag:
        note_lines.append("Travel surcharge may apply (outside A4/A2) — we’ll confirm before booking.")

    # Minimum charge
    if total < MIN_CHARGE:
        breakdown.append(f"Minimum charge applied: £{MIN_CHARGE}")
        total = MIN_CHARGE

    # VAT (currently 0 per your setup)
    vat_rate = PRICES.get("vat", 0)
    vat_amount = round(total * vat_rate, 2)
    if vat_rate > 0:
        breakdown.append(f"VAT ({int(vat_rate*100)}%): £{vat_amount}")

    grand_total = round(total + vat_amount, 2)

    return grand_total, breakdown, note_lines

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/quote", methods=["POST"])
def quote():
    data = {
        "postcode": request.form.get("postcode", "").strip(),
        "cleaning_type": request.form["cleaning_type"],
        "property_type": request.form["property_type"],
        "bedrooms": int(request.form["bedrooms"]),
        "bathrooms": int(request.form["bathrooms"]),
        "wc": int(request.form.get("wc", 0) or 0),
        "pets": request.form.get("pets", "No"),
        "pets_flag": bool(request.form.get("pets_flag")),
        "urgent_flag": bool(request.form.get("urgent_flag")),
        "travel_flag": bool(request.form.get("travel_flag")),
    }

    total, breakdown, notes = compute_total(
        data["cleaning_type"], data["property_type"], data["bedrooms"],
        data["bathrooms"], data["wc"], data["pets_flag"], data["urgent_flag"], data["travel_flag"]
    )

    # Build prompt to PRESENT fixed numbers (no changes)
    notes_text = "\n".join(f"- {n}" for n in notes) if notes else ""
    prompt = f"""
You are a pricing assistant for a London cleaning company.
Use EXACTLY these figures—do not change numbers or totals.

INPUT
- Postcode: {data['postcode']}
- Cleaning Type: {data['cleaning_type'].replace('_',' ').title()}
- Property Type: {data['property_type'].title()}
- Bedrooms: {data['bedrooms']}
- Bathrooms: {data['bathrooms']}
- WC: {data['wc']}
- Pets present: {"Yes" if data['pets_flag'] else "No"}
- Urgent booking: {"Yes" if data['urgent_flag'] else "No"}
- Travel flag: {"Yes" if data['travel_flag'] else "No"}

Calculated totals (fixed):
- Final Total: £{total}
- Breakdown lines:
{chr(10).join("- " + line for line in breakdown)}
{"\nAdditional notes:\n" + notes_text if notes_text else ""}

OUTPUT: return Markdown with sections:
## Quote
- Show the Final Total clearly and bullet the breakdown (use the lines above verbatim).

## Message
- Friendly 2–3 sentence note with CTA to book now (email: Seb.barrientos@hotmail.com or WhatsApp: 07526069139).
"""

    try:
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=700,
        )
        md = resp.choices[0].message.content.strip()
        html = markdown(md)
    except Exception as e:
        html = f"<p><strong>Error generating quote:</strong> {e}</p>"

    return render_template("result.html", result=html)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
