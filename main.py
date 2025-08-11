import json
from flask import Flask, render_template, request
from openai import OpenAI
from markdown import markdown

app = Flask(__name__)
client = OpenAI()

# Load prices at startup
with open("prices.json", "r") as f:
    PRICES = json.load(f)

def compute_total(cleaning_type, property_type, bedrooms, bathrooms, wc, pets, addons):
    breakdown = []
    total = 0.0

    if cleaning_type == "end_of_tenancy":
        if property_type == "studio":
            base = PRICES["end_of_tenancy"]["studio"]
        else:
            table = PRICES["end_of_tenancy"][property_type]
            base = table.get(str(bedrooms), 0)

        total += base
        breakdown.append(f"{cleaning_type.replace('_',' ').title()} base ({property_type}, {bedrooms} bed): £{base}")

        # extras
        if bathrooms and bathrooms > 1:  # assume first bath included in base
            extra_bath = (bathrooms - 1) * PRICES["end_of_tenancy"]["extra_bathroom"]
            total += extra_bath
            breakdown.append(f"Extra bathrooms ({bathrooms-1} × £{PRICES['end_of_tenancy']['extra_bathroom']}): £{extra_bath}")

        if wc and wc > 0:
            wc_cost = wc * PRICES["end_of_tenancy"]["extra_wc"]
            total += wc_cost
            breakdown.append(f"Extra WC ({wc} × £{PRICES['end_of_tenancy']['extra_wc']}): £{wc_cost}")

    elif cleaning_type == "airbnb":
        if property_type == "studio":
            base = PRICES["airbnb"]["studio"]
        else:
            table = PRICES["airbnb"][property_type]
            base = table.get(str(bedrooms), 0)
        total += base
        breakdown.append(f"Airbnb base ({property_type}, {bedrooms} bed): £{base}")

        if bathrooms and bathrooms > 1:  # extra bathrooms priced
            extra_bath = (bathrooms - 1) * PRICES["airbnb"]["extra_bathroom"]
            total += extra_bath
            breakdown.append(f"Extra bathrooms ({bathrooms-1} × £{PRICES['airbnb']['extra_bathroom']}): £{extra_bath}")

    # add‑ons
    for key in addons:
        price = PRICES["optional_addons"].get(key)
        if price:
            total += price
            breakdown.append(f"{key.replace('_',' ').title()}: £{price}")

    # pets doesn’t change price in your list—left for future rules
    if pets.lower().startswith("y"):
        breakdown.append("Note: Pet‑friendly team assigned.")

    # VAT if any
    vat_rate = PRICES.get("vat", 0)
    vat_amount = round(total * vat_rate, 2)
    if vat_rate > 0:
        breakdown.append(f"VAT ({int(vat_rate*100)}%): £{vat_amount}")
    grand_total = round(total + vat_amount, 2)

    return grand_total, breakdown

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/quote", methods=["POST"])
def quote():
    data = {
        "postcode": request.form.get("postcode","").strip(),
        "cleaning_type": request.form["cleaning_type"],
        "property_type": request.form["property_type"],
        "bedrooms": int(request.form["bedrooms"]),
        "bathrooms": int(request.form["bathrooms"]),
        "wc": int(request.form.get("wc", 0) or 0),
        "pets": request.form["pets"],
        "addons": request.form.getlist("addons")
    }

    total, breakdown = compute_total(
        data["cleaning_type"], data["property_type"], data["bedrooms"],
        data["bathrooms"], data["wc"], data["pets"], data["addons"]
    )

    # Ask the model to PRESENT (not change) your numbers
    upsell_lines = []
    for key, price in PRICES["optional_addons"].items():
        label = key.replace("_"," ").title()
        upsell_lines.append(f"- {label}: £{price}")
    upsell_text = "\n".join(upsell_lines[:6])

    prompt = f"""
You are a pricing assistant for a London cleaning company.
Use EXACTLY these figures—do not change prices or totals.

INPUT
- Postcode: {data['postcode']}
- Cleaning Type: {data['cleaning_type']}
- Property Type: {data['property_type']}
- Bedrooms: {data['bedrooms']}
- Bathrooms: {data['bathrooms']}
- WC: {data['wc']}
- Pets: {data['pets']}

Calculated totals (fixed):
- Final Total: £{total}
- Breakdown lines:
{chr(10).join("- " + line for line in breakdown)}

Optional add-ons (for context only; recommend max 2 relevant):
{upsell_text}

OUTPUT: return Markdown with sections:
## Quote
- Show the Final Total clearly and bullet the breakdown (use the lines above verbatim).

## Optional Upsells
- Suggest up to 2 relevant add-ons with their exact prices.
- Keep to a short sentence each.

## Message
- Friendly 2–3 sentence note with CTA to book now.
"""

    try:
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role":"user","content": prompt}],
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
