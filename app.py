from flask import Flask, request, redirect, url_for, render_template
import os
import pandas as pd
import re  # Import the regular expression module for parsing descriptions
from werkzeug.utils import secure_filename

app = Flask(__name__)

# Set the folder to store uploaded files
UPLOAD_FOLDER = 'uploads/'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # Limit file size to 16MB

# Allowed file extensions (only .csv for now)
ALLOWED_EXTENSIONS = {'csv'}

# Function to check if the file extension is allowed
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def extract_option_details(description):
    """
    Extract the option details (ticker, expiration, type, strike price) from the description column.
    Example description format: "PLTR 01/19/24 C 25"
    """
    match = re.search(r'(\w+)\s+(\d{2}/\d{2}/\d{2})\s+([PC])\s+(\d+)', description)
    if match:
        ticker, expiration, option_type, strike = match.groups()
        return ticker, expiration, option_type, strike
    return None, None, None, None

def analyze_performance(data):
    # Clean data: Remove $ signs and parentheses
    data['Price'] = pd.to_numeric(data['Price'].replace('[\\$,]', '', regex=True), errors='coerce')
    data['Quantity'] = pd.to_numeric(data['Quantity'], errors='coerce')
    data['Amount'] = data['Amount'].replace('[\\$,]', '', regex=True)
    data['Amount'] = data['Amount'].str.replace(r'\(([^)]+)\)', r'-\1')
    data['Amount'] = pd.to_numeric(data['Amount'], errors='coerce')

    # Separate stock and option transactions
    stock_buy_transactions = data[data['Trans Code'].str.contains('Buy', na=False)]
    stock_sell_transactions = data[data['Trans Code'].str.contains('Sell', na=False)]
    option_buy_transactions = data[data['Trans Code'].str.contains('BTO', na=False)]
    option_sell_transactions = data[data['Trans Code'].str.contains('STO', na=False)]
    ach_transactions = data[data['Trans Code'].str.contains('ACH', na=False)]

    performance = {}
    unresolved = []

    # Handle stock transactions (Buy/Sell)
    for ticker in stock_buy_transactions['Instrument'].dropna().unique():
        total_profit_loss = 0
        total_quantity = 0
        buys = stock_buy_transactions[stock_buy_transactions['Instrument'] == ticker]
        sells = stock_sell_transactions[stock_sell_transactions['Instrument'] == ticker]

        if not buys.empty and not sells.empty:
            total_bought = (buys['Quantity'] * buys['Price']).sum()
            total_sold = (sells['Quantity'] * sells['Price']).sum()
            profit_loss = total_sold - total_bought
            total_quantity = buys['Quantity'].sum()

            performance[ticker] = {
                'Total Quantity': total_quantity,
                'Total Profit/Loss': profit_loss,
                'Return %': (profit_loss / total_bought) * 100 if total_bought != 0 else 0
            }
        else:
            unresolved.append(ticker)

    # Handle options transactions (BTO/STO) based on expiration and strike price
    for _, row in option_buy_transactions.iterrows():
        ticker, expiration, option_type, strike = extract_option_details(row['Description'])
        if ticker:
            # Find corresponding STO (sell to open)
            matching_sell = option_sell_transactions[
                (option_sell_transactions['Description'].str.contains(expiration)) &
                (option_sell_transactions['Instrument'] == ticker)
            ]

            if not matching_sell.empty:
                total_bought = row['Quantity'] * row['Price']
                total_sold = (matching_sell['Quantity'] * matching_sell['Price']).sum()
                profit_loss = total_sold - total_bought
                total_quantity = row['Quantity']

                key = f"{ticker} {expiration} {option_type} {strike}"
                performance[key] = {
                    'Total Quantity': total_quantity,
                    'Total Profit/Loss': profit_loss,
                    'Return %': (profit_loss / total_bought) * 100 if total_bought != 0 else 0
                }
            else:
                unresolved.append(f"{ticker} {expiration} {option_type} {strike}")

    return performance, unresolved

def generate_summary(performance, unresolved):
    summary = "<h2>Portfolio Performance Summary</h2><ul>"

    total_profit = 0
    for ticker, data in performance.items():
        total_profit += data['Total Profit/Loss']
        summary += f"<li><strong>{ticker}</strong>: {data['Total Quantity']} shares/contracts, Profit/Loss: ${data['Total Profit/Loss']:.2f}, Return: {data['Return %']:.2f}%</li>"

    summary += f"<li><strong>Total Portfolio Profit/Loss</strong>: ${total_profit:.2f}</li></ul>"

    if unresolved:
        summary += "<h3>Unresolved Trades</h3><ul>"
        for unresolved_trade in unresolved:
            summary += f"<li>{unresolved_trade}</li>"
        summary += "</ul>"

    # Write-up based on performance
    write_up = f"<h3>Overall Insights</h3><p>The total portfolio performance shows a net {'profit' if total_profit > 0 else 'loss'} of ${total_profit:.2f}. "

    if total_profit > 0:
        write_up += "The strategy appears to be profitable with significant gains from well-timed buys and sells."
    else:
        write_up += "The portfolio has experienced losses, which may be attributed to some underperforming trades or option expirations."

    return summary + write_up

@app.route('/', methods=['GET', 'POST'])
def upload_file():
    if request.method == 'POST':
        # Check if a file was uploaded
        if 'file' not in request.files:
            return 'No file part'
        file = request.files['file']
        # If the user does not select a file
        if file.filename == '':
            return 'No selected file'
        # If the file is allowed, save it to the upload folder
        if file and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(file_path)

            try:
                # Parse the CSV file with pandas, skip bad lines
                data = pd.read_csv(file_path, on_bad_lines='skip')

                # Extract key columns for display
                if all(col in data.columns for col in ['Settle Date', 'Instrument', 'Trans Code', 'Quantity', 'Price', 'Amount', 'Description']):
                    # Analyze performance
                    performance, unresolved = analyze_performance(data)

                    # Generate summary
                    summary_html = generate_summary(performance, unresolved)

                    # Extract relevant columns and display
                    extracted_data = data[['Settle Date', 'Instrument', 'Trans Code', 'Quantity', 'Price', 'Amount', 'Description']]
                    extracted_data_html = extracted_data.to_html(index=False)  # Convert to HTML without the index column

                    # Displaying the summary, unresolved, and extracted data in the web app
                    return f'<h1>File {filename} uploaded successfully!</h1>{summary_html}<h2>Trade Data:</h2>{extracted_data_html}'
                else:
                    return 'Error: Required columns not found in the file.'
            except pd.errors.ParserError:
                return 'There was an error parsing the CSV file. Please check the format and try again.'

        else:
            return 'Invalid file format. Please upload a CSV file.'

    # This will display the form for GET requests
    return '''
        <h1>Upload your Robinhood Trade Report</h1>
        <form method="post" enctype="multipart/form-data">
            <input type="file" name="file">
            <input type="submit">
        </form>
    '''

if __name__ == '__main__':
    if not os.path.exists(UPLOAD_FOLDER):
        os.makedirs(UPLOAD_FOLDER)  # Create folder if it doesn't exist
    app.run(debug=True, host='0.0.0.0', port=5001)
