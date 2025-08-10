
from flask import Flask, render_template, request
import openai
import os

app = Flask(__name__)
openai.api_key = os.getenv("OPENAI_API_KEY")

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/quote', methods=['POST'])
def quote():
    postcode = request.form['postcode']
    bedrooms = request.form['bedrooms']
    bathrooms = request.form['bathrooms']
    pets = request.form['pets']
    cleaning_type = request.form['cleaning_type']

    prompt = f'''
You are a cleaning quote assistant.

Given:
- Location: {postcode}
- Bedrooms: {bedrooms}
- Bathrooms: {bathrooms}
- Pets: {pets}
- Cleaning type: {cleaning_type}

Generate:
1. A quote in GBP
2. Two optional upsells
3. Friendly, persuasive language
    '''

    try:
        response = openai.ChatCompletion.create(
            model="gpt-4",
            messages=[{"role": "user", "content": prompt}]
        )
        result = response.choices[0].message['content'].strip()
    except Exception as e:
        result = f"Error generating quote: {e}"

    return render_template('result.html', result=result)
