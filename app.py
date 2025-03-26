from flask import Flask, request, jsonify, render_template, send_from_directory
import requests
import sqlite3
import re
import os
import pandas as pd
import sweetviz as sv
import uuid
import matplotlib
matplotlib.use('Agg')  # Set the backend before importing pyplot
import matplotlib.pyplot as plt
import random
from threading import Lock

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'uploads'
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

OLLAMA_API_URL = "http://localhost:11434/api/generate"
sv.config_parser.read_string("[General]\nuse_threads = False")  # Disable Sweetviz threading

# Sample quiz questions for fallback
SAMPLE_QUIZZES = [
    {
        "question": "What is the most popular database system?",
        "options": ["MySQL", "PostgreSQL", "SQLite", "MongoDB"],
        "correct": "MySQL"
    },
    {
        "question": "What does SQL stand for?",
        "options": ["Structured Query Language", "Simple Query Language", "Standard Query Language", "System Query Language"],
        "correct": "Structured Query Language"
    }
]

def generate_sql(question, table_name, column_names):
    # Create a detailed schema description
    schema_info = f"TABLE {table_name} ({', '.join(column_names)})"
    
    prompt = f"""
You are a SQL expert. Given this database schema:
{schema_info}

Convert this natural language question to a precise SQL query:
"{question}"

Important rules:
1. Strictly return the SQL query, no additional text
2. Use the exact table and column names provided
3. If filtering is needed, use appropriate WHERE clauses
4. For aggregations, use GROUP BY where needed

SQL Query:
"""
    
    payload = {
        "model": "codellama",
        "prompt": prompt,
        "stream": False
    }
    
    try:
        response = requests.post(OLLAMA_API_URL, json=payload)
        if response.status_code == 200:
            sql = response.json()["response"]
            # Remove any markdown code blocks
            sql = re.sub(r'```sql|```', '', sql).strip()
            return sql
        return f"Error: {response.text}"
    except Exception as e:
        return f"Error: {str(e)}"

def get_column_names(database_path, table_name):
    conn = sqlite3.connect(database_path)
    cursor = conn.cursor()
    cursor.execute(f"PRAGMA table_info({table_name})")
    columns = cursor.fetchall()
    conn.close()
    return [column[1] for column in columns]

def get_table_names(database_path):
    conn = sqlite3.connect(database_path)
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = cursor.fetchall()
    conn.close()
    return [table[0] for table in tables]

def clean_sql_query(sql_query):
    return re.sub(r'```|`', '', sql_query).strip()

def execute_sql(database_path, sql_query):
    conn = sqlite3.connect(database_path)
    cursor = conn.cursor()
    try:
        cursor.execute(sql_query)
        results = cursor.fetchall()
        columns = [description[0] for description in cursor.description] if cursor.description else []
        return {"columns": columns, "rows": results}
    except sqlite3.Error as e:
        return {"error": str(e)}
    finally:
        conn.close()

def generate_sweetviz_report(results):
    df = pd.DataFrame(results["rows"], columns=results["columns"])
    report = sv.analyze(df)
    report_path = os.path.join(app.config['UPLOAD_FOLDER'], f'report_{uuid.uuid4()}.html')
    report.show_html(filepath=report_path, open_browser=False)
    return report_path

def generate_dynamic_queries(df):
    queries = []
    numeric_cols = df.select_dtypes(include=['int64', 'float64']).columns
    cat_cols = df.select_dtypes(include=['object']).columns
    date_cols = [col for col in df.columns if 'date' in col.lower()]

    if len(numeric_cols) > 0:
        queries.append(f"Histogram of {numeric_cols[0]}")
    if len(cat_cols) > 0:
        queries.append(f"Bar chart of {cat_cols[0]}")
    if len(date_cols) > 0 and len(numeric_cols) > 0:
        queries.append(f"Line chart of {numeric_cols[0]} over time")
    if len(numeric_cols) > 1:
        queries.append(f"Scatter plot of {numeric_cols[0]} vs {numeric_cols[1]}")
    
    while len(queries) < 4:
        col = df.columns[len(queries) % len(df.columns)]
        queries.append(f"Box plot of {col}")
    
    return queries[:4]

def generate_python_code(query):
    prompt = f"Generate Python code using matplotlib/seaborn to create: {query}\nOnly provide the code."
    response = requests.post(OLLAMA_API_URL, json={
        "model": "codellama",
        "prompt": prompt,
        "stream": False
    })
    return response.json()["response"] if response.status_code == 200 else ""

def execute_python_code(code, df):
    try:
        plt.figure()
        exec_globals = {'df': df, 'plt': plt, 'pd': pd}
        exec(code, exec_globals)
        image_path = os.path.join(app.config['UPLOAD_FOLDER'], f'viz_{uuid.uuid4()}.png')
        plt.savefig(image_path)
        plt.close()
        return image_path
    except Exception as e:
        print(f"Visualization error: {e}")
        return None

@app.route('/')
def home():
    return render_template('index.html')

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
        return jsonify({
            "message": "File uploaded successfully",
            "file_path": file_path,
            "table_names": get_table_names(file_path)
        })
    return jsonify({"error": "Only .db files allowed"}), 400

@app.route('/generate-sql', methods=['POST'])
def generate_and_execute_sql():
    data = request.json
    question = data.get('question')
    database_path = data.get('database_path')
    table_name = data.get('table_name')

    if not os.path.exists(database_path):
        return jsonify({"error": "Database not found"}), 404

    # Get column names for the specified table
    column_names = get_column_names(database_path, table_name)
    
    # Generate SQL with table name and columns
    sql_query = generate_sql(question, table_name, column_names)
    cleaned_sql = clean_sql_query(sql_query)
    
    # Execute the query
    results = execute_sql(database_path, cleaned_sql)

    report_path = None
    if "error" not in results:
        report_path = generate_sweetviz_report(results)

    return jsonify({
        "sql_query": cleaned_sql,
        "results": results,
        "report_path": report_path,
        "progress": 40
    })

@app.route('/generate-visualizations', methods=['POST'])
def generate_visualizations():
    data = request.json
    results = data.get('results')
    
    if not results or "error" in results:
        return jsonify({"error": "Invalid results data"}), 400

    try:
        # Convert results to DataFrame
        df = pd.DataFrame(results["rows"], columns=results["columns"])
        
        # Generate visualization queries
        queries = generate_dynamic_queries(df)
        visualizations = []
        
        # Set matplotlib backend explicitly
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        
        for query in queries:
            try:
                # Generate Python code
                code = generate_python_code(query)
                if not code or "Error" in code:
                    continue
                
                # Create new figure for each plot
                plt.figure(figsize=(8, 6))
                
                # Prepare execution environment
                exec_globals = {
                    'df': df,
                    'plt': plt,
                    'pd': pd
                }
                
                # Add seaborn if needed
                if 'sns' in code:
                    import seaborn as sns
                    exec_globals['sns'] = sns
                
                # Execute the code
                exec(code, exec_globals)
                
                # Save the visualization
                img_name = f"viz_{uuid.uuid4().hex}.png"
                img_path = os.path.join(app.config['UPLOAD_FOLDER'], img_name)
                plt.savefig(img_path, bbox_inches='tight')
                plt.close()
                
                visualizations.append(img_name)
                
            except Exception as e:
                print(f"Failed to generate {query}: {str(e)}")
                plt.close()
                continue

        return jsonify({
            "queries": queries,
            "visualizations": visualizations,
            "message": f"Generated {len(visualizations)} visualizations"
        })

    except Exception as e:
        return jsonify({"error": f"Visualization generation failed: {str(e)}"}), 500
        
@app.route('/get-quiz', methods=['GET'])
def get_quiz():
    try:
        response = requests.get('https://opentdb.com/api.php?amount=1&type=multiple', timeout=2)
        if response.status_code == 200:
            quiz = response.json()['results'][0]
            return jsonify({
                "question": quiz['question'],
                "options": quiz['incorrect_answers'] + [quiz['correct_answer']],
                "correct": quiz['correct_answer']
            })
    except:
        pass
    
    # Fallback to sample quiz if API fails
    quiz = random.choice(SAMPLE_QUIZZES)
    return jsonify(quiz)

if __name__ == '__main__':
    app.run(debug=True)