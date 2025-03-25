from flask import Flask, request, jsonify, render_template
import requests
import sqlite3
import re
import os
import pandas as pd
import sweetviz as sv

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'uploads'
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# Define the Ollama API endpoint
OLLAMA_API_URL = "http://localhost:11434/api/generate"

# Function to generate SQL queries using Ollama
def generate_sql(question):
    payload = {
        "model": "codellama",  # Replace with the model you downloaded
        "prompt": f"Convert the following natural language question to SQL:\n\n{question}\n\nSQL Query:",
        "stream": False
    }
    response = requests.post(OLLAMA_API_URL, json=payload)
    if response.status_code == 200:
        return response.json()["response"]
    else:
        return f"Error: {response.status_code} - {response.text}"

# Function to extract column names
def get_column_names(database_path, table_name):
    conn = sqlite3.connect(database_path)
    cursor = conn.cursor()
    cursor.execute(f"PRAGMA table_info({table_name})")
    columns = cursor.fetchall()
    conn.close()
    column_names = [column[1] for column in columns]
    return column_names

# Function to get all table names from the database
def get_table_names(database_path):
    conn = sqlite3.connect(database_path)
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = cursor.fetchall()
    conn.close()
    return [table[0] for table in tables]

# Function to generate a prompt
def generate_prompt(question, table_name, column_names):
    schema_info = f"Table: {table_name}\nColumns: {', '.join(column_names)}"
    prompt = f"""
Given the following database schema:
{schema_info}

Question: {question}. Only give the SQL Query, don't write anything else.
SQL Query:
"""
    return prompt

# Function to clean the SQL query
def clean_sql_query(sql_query):
    sql_query = re.sub(r'```', '', sql_query)  # Remove triple backticks
    sql_query = re.sub(r'`', '', sql_query)    # Remove single backticks
    sql_query = sql_query.strip()              # Remove leading/trailing whitespace
    return sql_query

# Function to execute SQL queries
def execute_sql(database_path, sql_query):
    conn = sqlite3.connect(database_path)
    cursor = conn.cursor()
    try:
        cursor.execute(sql_query)
        results = cursor.fetchall()
        columns = [description[0] for description in cursor.description] if cursor.description else []
        conn.commit()  # Commit changes for UPDATE/INSERT/DELETE queries
        return {"columns": columns, "rows": results}
    except sqlite3.Error as e:
        return {"error": str(e)}  # Return error message
    finally:
        conn.close()

# Function to generate Sweetviz report
def generate_sweetviz_report(results):
    # Convert results to a Pandas DataFrame
    df = pd.DataFrame(results["rows"], columns=results["columns"])
    
    # Generate Sweetviz report
    report = sv.analyze(df)
    report_path = os.path.join(app.config['UPLOAD_FOLDER'], 'sweetviz_report.html')
    report.show_html(filepath=report_path, open_browser=False)
    
    return report_path

# Route for the home page
@app.route('/')
def home():
    return render_template('index.html')

# Route to handle file upload
@app.route('/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        return jsonify({"error": "No file uploaded"}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "No file selected"}), 400

    if file and file.filename.endswith('.db'):
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], file.filename)
        file.save(file_path)
        table_names = get_table_names(file_path)  # Get table names
        return jsonify({
            "message": "File uploaded successfully",
            "file_path": file_path,
            "table_names": table_names  # Return table names
        })
    else:
        return jsonify({"error": "Invalid file type. Only .db files are allowed"}), 400

# Route to handle SQL generation and execution
@app.route('/generate-sql', methods=['POST'])
def generate_and_execute_sql():
    data = request.json
    question = data.get('question')
    database_path = data.get('database_path')
    table_name = data.get('table_name')

    if not os.path.exists(database_path):
        return jsonify({"error": "Database file not found"}), 404

    # Extract column names
    column_names = get_column_names(database_path, table_name)

    # Generate prompt with schema information
    prompt = generate_prompt(question, table_name, column_names)

    # Generate SQL query
    sql_query = generate_sql(prompt)

    # Clean the SQL query
    cleaned_sql_query = clean_sql_query(sql_query)

    # Execute the cleaned SQL query
    results = execute_sql(database_path, cleaned_sql_query)

    # Generate Sweetviz report if results are valid
    if "error" not in results:
        report_path = generate_sweetviz_report(results)
        with open(report_path, 'r') as file:
            report_html = file.read()
    else:
        report_html = None

    return jsonify({
        "sql_query": cleaned_sql_query,
        "results": results,
        "report_html": report_html  # Send Sweetviz report HTML to frontend
    })

if __name__ == '__main__':
    app.run(debug=True)