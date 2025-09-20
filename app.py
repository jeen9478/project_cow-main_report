from flask import Flask, render_template, request, redirect, url_for, session, send_file, Response, jsonify
import sqlite3, json, os, io, requests
from datetime import datetime
import xlsxwriter
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Image as RLImage
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from PIL import Image


# Register ฟอนต์ไทย


app = Flask(__name__)
app.secret_key = 'secret_key'

DB = "cow_data.db"
USERS_FILE = "users.json"
PI_STREAM_BASE = os.getenv("PI_STREAM_BASE", "http://192.168.1.166:5001")

# ---------------- Telegram Config ----------------
TELEGRAM_TOKEN = "8319289537:AAHFg85Qv1dtVNh9M1F8d6uaoqwS_sF2yIg"
TELEGRAM_CHAT_ID = "6316752016"
TEMP_THRESHOLD = 41.0  # เกณฑ์อุณหภูมิ (°C)

def send_telegram_alert(temp, image_path=None):
    """ส่งข้อความแจ้งเตือน + แนบรูป (ถ้ามี) ไป Telegram"""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    msg = f"แจ้งเตือน! พบอุณหภูมิสูงเกินเกณฑ์\n{temp:.2f} °C"

    try:
        r1 = requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": msg})
        print(" sendMessage:", r1.status_code, r1.text)

        if image_path and os.path.exists(image_path):
            url_photo = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"
            with open(image_path, "rb") as f:
                r2 = requests.post(url_photo, data={"chat_id": TELEGRAM_CHAT_ID}, files={"photo": f})
                print("sendPhoto:", r2.status_code, r2.text)

        print("ส่งแจ้งเตือน Telegram แล้ว")
    except Exception as e:
        print("ส่งแจ้งเตือนไม่สำเร็จ:", e)

# -------------------- DB helpers -------------------------
def get_db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn

def get_cow_data(start_date=None, end_date=None):
    conn = get_db()
    query = "SELECT id, temperature, timestamp, image_path FROM cow_data WHERE 1=1"
    params = []

    if start_date:
        query += " AND date(timestamp) >= date(?)"
        params.append(start_date)
    if end_date:
        query += " AND date(timestamp) <= date(?)"
        params.append(end_date)

    query += " ORDER BY timestamp DESC"
    rows = conn.execute(query, params).fetchall()
    conn.close()

    return rows

def get_report_data(start_date=None, end_date=None):
    conn = get_db()
    query = """
        SELECT a.rfid_tag, a.cow_name, a.gender, a.breed,
               c.temperature, c.timestamp, c.image_path
        FROM cow_data c
        LEFT JOIN animals a ON c.animal_id = a.id
        WHERE 1=1
    """
    params = []

    if start_date:
        query += " AND date(c.timestamp) >= date(?)"
        params.append(start_date)
    if end_date:
        query += " AND date(c.timestamp) <= date(?)"
        params.append(end_date)

    query += " ORDER BY c.timestamp DESC"

    rows = conn.execute(query, params).fetchall()
    conn.close()

    data = []
    count_normal, count_high = 0, 0
    for r in rows:
        status = "สูง" if r["temperature"] >= 41 else "ปกติ"
        if status == "สูง":
            count_high += 1
        else:
            count_normal += 1

        data.append({
            "rfid_tag": r["rfid_tag"],
            "name": r["cow_name"],
            "gender": r["gender"],
            "breed": r["breed"],
            "temperature": r["temperature"],
            "status": status,
            "timestamp": r["timestamp"],
            "image_path": r["image_path"]
        })

    return data, count_normal, count_high


def get_data(date=None, temp_min=None, start_time=None, end_time=None):
    conn = get_db()
    q = "SELECT * FROM cow_data WHERE temperature >= ?"
    params = [TEMP_THRESHOLD]  # ดึงเฉพาะที่ ≥ 41
    if date:
        q += " AND DATE(timestamp)=?"
        params.append(date)
    if temp_min:
        q += " AND temperature>=?"
        params.append(temp_min)
    if start_time:
        q += " AND time(timestamp)>=?"
        params.append(start_time)
    if end_time:
        q += " AND time(timestamp)<=?"
        params.append(end_time)
    q += " ORDER BY id DESC"
    rows = conn.execute(q, params).fetchall()
    conn.close()
    return rows

def load_users():
    if os.path.exists(USERS_FILE):
        with open(USERS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_users(users):
    with open(USERS_FILE, "w", encoding="utf-8") as f:
        json.dump(users, f, ensure_ascii=False, indent=4)

# -------------------- Routes: auth ------------------------
@app.route('/')
def home():
    return redirect(url_for('login'))

@app.route('/login', methods=['GET','POST'])
def login():
    if request.method == 'POST':
        u = request.form['username'].strip()
        p = request.form['password']
        users = load_users()
        if u in users and users[u]['password'] == p:
            session['user'] = u
            session['role'] = users[u]['role']
            return redirect(url_for('dashboard'))
        return "ชื่อผู้ใช้หรือรหัสผ่านไม่ถูกต้อง"
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/register', methods=['POST'])
def register():
    username = request.form.get('new_username', '').strip()
    password = request.form.get('new_password', '').strip()
    if not username or not password:
        return "กรุณากรอกชื่อผู้ใช้และรหัสผ่านให้ครบถ้วน", 400
    users = load_users()
    if username in users:
        return "ชื่อผู้ใช้นี้ถูกใช้งานแล้ว", 400
    users[username] = {"password": password, "role": "user"}
    save_users(users)
    return redirect(url_for('login'))
#add cow
@app.route('/add_cow', methods=['GET', 'POST'])
def add_cow():
    if request.method == 'POST':
        cow_name = request.form['cow_name']
        rfid_tag = request.form['rfid_tag']

        conn = sqlite3.connect('cow_data.db')
        cursor = conn.cursor()
        cursor.execute("INSERT INTO cow_info (cow_name, rfid_tag) VALUES (?, ?)", (cow_name, rfid_tag))
        conn.commit()
        conn.close()

        return redirect(url_for('dashboard'))

    return render_template('add_cow.html')

# -------------------- Dashboard ---------------------------
@app.route('/dashboard')
def dashboard():
    if 'user' not in session:
        return redirect(url_for('login'))

    date = request.args.get('date')
    start_hour = request.args.get('start_hour')
    start_min = request.args.get('start_min')
    end_hour = request.args.get('end_hour')
    end_min = request.args.get('end_min')

    # รวมเวลา
    start_time = f"{start_hour}:{start_min}" if start_hour and start_min else None
    end_time   = f"{end_hour}:{end_min}" if end_hour and end_min else None

    # ดึงข้อมูล (อาจจะว่าง)
    data = get_data(date=date, start_time=start_time, end_time=end_time)

    return render_template(
        'dashboard.html',
        data=data,
        active_page='dashboard',
        role=session.get('role'),
        username=session.get('user'),
        date_val=date,
        start_hour_val=start_hour,
        start_min_val=start_min,
        end_hour_val=end_hour,
        end_min_val=end_min
    )


# -------------------- Download Excel ----------------------
@app.route("/download_xlsx")
def download_xlsx():
    cows, count_normal, count_high = get_report_data()

    output = io.BytesIO()
    workbook = xlsxwriter.Workbook(output, {'in_memory': True})
    worksheet = workbook.add_worksheet("รายงานวัว")

    headers = ["แท็ก", "ชื่อ", "เพศ", "พันธุ์", "อุณหภูมิ (°C)", "สถานะ", "เวลา", "ภาพ"]
    for col, h in enumerate(headers):
        worksheet.write(0, col, h)

    worksheet.set_column("A:A", 15)
    worksheet.set_column("B:B", 15)
    worksheet.set_column("C:C", 10)
    worksheet.set_column("D:D", 15)
    worksheet.set_column("E:E", 15)
    worksheet.set_column("F:F", 10)
    worksheet.set_column("G:G", 20)
    worksheet.set_column("H:H", 25)

    row = 1
    for cow in cows:
        worksheet.write(row, 0, cow["rfid_tag"])
        worksheet.write(row, 1, cow["name"])
        worksheet.write(row, 2, cow["gender"])
        worksheet.write(row, 3, cow["breed"])
        worksheet.write(row, 4, cow["temperature"])
        worksheet.write(row, 5, cow["status"])
        worksheet.write(row, 6, cow["timestamp"])

        if cow["image_path"] and os.path.exists(cow["image_path"]):
            with Image.open(cow["image_path"]) as img:
                w, h = img.size
            cell_width_px = 100
            cell_height_px = 80
            x_scale = cell_width_px / w
            y_scale = cell_height_px / h
            scale = min(x_scale, y_scale)
            worksheet.set_row(row, cell_height_px)
            worksheet.insert_image(row, 7, cow["image_path"], {
                "x_scale": scale,
                "y_scale": scale,
                "positioning": 1
            })
        row += 1

    # ✅ สรุปท้ายไฟล์
    worksheet.write(row+1, 0, f"จำนวนวัวอุณหภูมิปกติ: {count_normal}")
    worksheet.write(row+2, 0, f"จำนวนวัวอุณหภูมิสูง: {count_high}")

    workbook.close()
    output.seek(0)

    return send_file(output,
                     as_attachment=True,
                     download_name="cow_report.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

# -------------------- Users -------------------------------
@app.route('/users')
def users_page():
    if 'user' not in session or session.get('role') != 'admin':
        return redirect(url_for('login'))
    return render_template('users.html', users=load_users(), active_page='users')

@app.route('/add_user', methods=['GET','POST'])
def add_user():
    if 'user' not in session or session.get('role') != 'admin':
        return redirect(url_for('login'))
    if request.method == 'POST':
        u = request.form['username'].strip()
        p = request.form['password']
        r = request.form['role']
        users = load_users()
        if u in users:
            return "ผู้ใช้นี้มีอยู่แล้ว"
        users[u] = {'password': p, 'role': r}
        save_users(users)
        return redirect(url_for('users_page'))
    return render_template('add_user.html', active_page='users')

@app.route('/edit_user/<username>', methods=['GET','POST'])
def edit_user(username):
    if 'user' not in session or session.get('role') != 'admin':
        return redirect(url_for('login'))
    users = load_users()
    if username not in users:
        return "ไม่พบผู้ใช้"
    if request.method == 'POST':
        users[username]['password'] = request.form['password']
        users[username]['role'] = request.form['role']
        save_users(users)
        return redirect(url_for('users_page'))
    return render_template('edit_user.html', username=username, user=users[username], active_page='users')

@app.route('/delete_user/<username>')
def delete_user(username):
    if 'user' not in session or session.get('role') != 'admin':
        return redirect(url_for('login'))
    if username == session.get('user'):
        return "ลบตัวเองไม่ได้"
    users = load_users()
    if username in users:
        del users[username]
        save_users(users)
    return redirect(url_for('users_page'))

# -------------------- Upload API --------------------------
@app.route('/upload', methods=['POST'])
def upload():
    image = request.files.get('image')
    temperature = request.form.get('temperature')
    if not image or not temperature:
        return "Missing image or temperature", 400

    try:
        temp_val = float(temperature.strip())
    except:
        return "Invalid temperature", 400

    if temp_val < TEMP_THRESHOLD:
        print(f"ℹTemp {temp_val} °C < {TEMP_THRESHOLD}, not saved")
        return "ต่ำกว่าเกณฑ์ ไม่บันทึก", 200

    fname = f"thermal_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
    save_path = os.path.join('static', 'images', fname)
    os.makedirs(os.path.dirname(save_path), exist_ok=True)
    image.save(save_path)

    conn = get_db()
    conn.execute(
        "INSERT INTO cow_data (temperature, timestamp, image_path) VALUES (?, datetime('now','localtime'), ?)",
        (temp_val, save_path)
    )
    conn.commit()
    conn.close()

    send_telegram_alert(temp_val, save_path)
    print(f"Saved + Alert: {temp_val} °C")
    return "บันทึกสำเร็จ", 200

# -------------------- Delete Image ------------------------
@app.route('/delete_image/<int:image_id>', methods=['POST'])
def delete_image(image_id):
    if 'user' not in session:
        return jsonify({'ok': False, 'error': 'unauthorized'}), 401

    conn = get_db()
    row = conn.execute("SELECT image_path FROM cow_data WHERE id=?", (image_id,)).fetchone()
    if not row:
        conn.close()
        return jsonify({'ok': False, 'error': 'not_found'}), 404

    img_path = row['image_path']
    try:
        if img_path and os.path.exists(img_path):
            os.remove(img_path)
    except Exception:
        pass

    conn.execute("DELETE FROM cow_data WHERE id=?", (image_id,))
    conn.commit()
    conn.close()
    return jsonify({'ok': True})

# -------------------- Realtime ----------------------------
@app.route('/realtime')
def realtime_camera():
    if 'user' not in session:
        return redirect(url_for('login'))
    return render_template('realtime.html', active_page='realtime', pi_stream_base=PI_STREAM_BASE)

@app.route("/report", methods=["GET", "POST"])
def report_page():
    start_date = request.form.get("start_date")
    end_date = request.form.get("end_date")

    cows, count_normal, count_high = get_report_data(start_date, end_date)

    return render_template("report.html",
                           cows=cows,
                           count_normal=count_normal,
                           count_high=count_high,
                           start_date=start_date,
                           end_date=end_date,
                           active_page="report")


@app.route("/download_pdf")
def download_pdf():
    cows, count_normal, count_high = get_report_data()

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4)
    styles = getSampleStyleSheet()
    th_style = styles["Normal"]
    th_style.fontName = "Sarabun"
    th_style.fontSize = 12

    story = []
    story.append(Paragraph("รายงานข้อมูลวัว", th_style))
    story.append(Spacer(1, 12))

    # สร้างตารางหัว
    data = [["แท็ก", "ชื่อ", "เพศ", "พันธุ์", "อุณหภูมิ (°C)", "สถานะ", "เวลา", "ภาพ"]]

    for cow in cows:
        row = [
            cow["rfid_tag"] or "-",
            cow["name"] or "-",
            cow["gender"] or "-",
            cow["breed"] or "-",
            f"{cow['temperature']:.2f}" if cow["temperature"] else "-",
            cow["status"],
            cow["timestamp"],
            ""  # เว้นที่ไว้สำหรับภาพ
        ]
        data.append(row)

    table = Table(data, colWidths=[60, 60, 40, 60, 70, 50, 100, 80])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.lightgreen),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("FONTNAME", (0, 0), (-1, -1), "Sarabun"),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
    ]))
    story.append(table)
    story.append(Spacer(1, 12))

    # สรุปผล
    story.append(Paragraph(f"จำนวนวัวอุณหภูมิปกติ: {count_normal}", th_style))
    story.append(Paragraph(f"จำนวนวัวอุณหภูมิสูง: {count_high}", th_style))

    doc.build(story)
    buffer.seek(0)

    return send_file(buffer,
                     as_attachment=True,
                     download_name="cow_report.pdf",
                     mimetype="application/pdf")

pdfmetrics.registerFont(TTFont('Sarabun', 'fonts/Sarabun-Regular.ttf'))

@app.route("/details/<int:id>")
def view_details(id):
    if 'user' not in session:
        return redirect(url_for('login'))

    conn = get_db()
    row = conn.execute("SELECT * FROM cow_data WHERE id=?", (id,)).fetchone()
    conn.close()

    if not row:
        return "ไม่พบข้อมูล", 404

    # ชี้ไปที่ view.html
    return render_template("view.html", cow=row, active_page="dashboard")

# -------------------- Run App -----------------------------
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)

