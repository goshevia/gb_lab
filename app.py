import json
import os

def load_local_settings():
    try:
        with open("local_settings.json", "r") as f:
            return json.load(f)
    except:
        return {}

settings = load_local_settings()

print("API KEY:", OPENAI_API_KEY)
print("CLIENT:", client)

import random
import sqlite3
import time
import uuid
from pathlib import Path

from flask import Flask, jsonify, render_template, request
from openai import OpenAI

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / 'trainer.db'
PROFILES_PATH = BASE_DIR / 'profiles.json'
ACCESS_CODES_PATH = BASE_DIR / 'access_codes.json'

OPENAI_API_KEY = os.getenv('OPENAI_API_KEY') or settings.get('OPENAI_API_KEY')
BASE_URL = os.getenv('OPENAI_BASE_URL') or settings.get('OPENAI_BASE_URL', 'https://api.z.ai/api/paas/v4')
MODEL_NAME = os.getenv('OPENAI_MODEL') or settings.get('OPENAI_MODEL', 'GLM-4.7-Flash')
APP_SECRET = os.getenv('APP_SECRET') or settings.get('APP_SECRET', 'change-me-please')

MAX_USER_MESSAGES = int(os.getenv('MAX_USER_MESSAGES', '12'))
MAX_MESSAGE_CHARS = int(os.getenv('MAX_MESSAGE_CHARS', '600'))
RATE_LIMIT_WINDOW_SECONDS = int(os.getenv('RATE_LIMIT_WINDOW_SECONDS', '60'))
RATE_LIMIT_MAX_REQUESTS = int(os.getenv('RATE_LIMIT_MAX_REQUESTS', '20'))

app = Flask(__name__)
app.config['SECRET_KEY'] = APP_SECRET

client = OpenAI(
    api_key=OPENAI_API_KEY,
    base_url=BASE_URL
) if OPENAI_API_KEY else None


def db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = db_connection()
    cur = conn.cursor()
    cur.execute(
        '''
        CREATE TABLE IF NOT EXISTS access_codes (
            code TEXT PRIMARY KEY,
            is_active INTEGER NOT NULL DEFAULT 1,
            max_uses INTEGER NOT NULL DEFAULT 1,
            used_count INTEGER NOT NULL DEFAULT 0,
            expires_at TEXT
        )
        '''
    )
    cur.execute(
        '''
        CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY,
            participant_name TEXT NOT NULL,
            access_code TEXT NOT NULL,
            profile_id TEXT NOT NULL,
            profile_label TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            finished_at INTEGER,
            ip_address TEXT,
            guessed_type TEXT,
            total_user_messages INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY(access_code) REFERENCES access_codes(code)
        )
        '''
    )
    cur.execute(
        '''
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            text TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            FOREIGN KEY(session_id) REFERENCES sessions(id)
        )
        '''
    )
    cur.execute(
        '''
        CREATE TABLE IF NOT EXISTS evaluations (
            session_id TEXT PRIMARY KEY,
            score_total INTEGER,
            strengths TEXT,
            improvements TEXT,
            summary TEXT,
            raw_json TEXT,
            created_at INTEGER NOT NULL,
            FOREIGN KEY(session_id) REFERENCES sessions(id)
        )
        '''
    )
    cur.execute(
        '''
        CREATE TABLE IF NOT EXISTS request_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ip_address TEXT NOT NULL,
            action TEXT NOT NULL,
            created_at INTEGER NOT NULL
        )
        '''
    )
    conn.commit()
    conn.close()
    seed_access_codes()


def seed_access_codes():
    if not ACCESS_CODES_PATH.exists():
        return
    with open(ACCESS_CODES_PATH, 'r', encoding='utf-8') as f:
        codes = json.load(f)
    conn = db_connection()
    cur = conn.cursor()
    for item in codes:
        cur.execute(
            '''
            INSERT OR IGNORE INTO access_codes(code, is_active, max_uses, used_count, expires_at)
            VALUES (?, ?, ?, ?, ?)
            ''',
            (
                item['code'],
                1 if item.get('is_active', True) else 0,
                int(item.get('max_uses', 1)),
                int(item.get('used_count', 0)),
                item.get('expires_at'),
            ),
        )
    conn.commit()
    conn.close()


def load_profiles():
    with open(PROFILES_PATH, 'r', encoding='utf-8') as f:
        return json.load(f)


def now_ts():
    return int(time.time())


def get_ip():
    forwarded = request.headers.get('X-Forwarded-For', '')
    if forwarded:
        return forwarded.split(',')[0].strip()
    return request.remote_addr or 'unknown'


def log_request(ip_address: str, action: str):
    conn = db_connection()
    cur = conn.cursor()
    cur.execute(
        'INSERT INTO request_log(ip_address, action, created_at) VALUES (?, ?, ?)',
        (ip_address, action, now_ts()),
    )
    conn.commit()
    conn.close()


def check_rate_limit(ip_address: str):
    cutoff = now_ts() - RATE_LIMIT_WINDOW_SECONDS
    conn = db_connection()
    cur = conn.cursor()
    cur.execute(
        'SELECT COUNT(*) AS cnt FROM request_log WHERE ip_address = ? AND created_at >= ?',
        (ip_address, cutoff),
    )
    row = cur.fetchone()
    conn.close()
    if row['cnt'] >= RATE_LIMIT_MAX_REQUESTS:
        return False
    return True


def get_session(session_id: str):
    conn = db_connection()
    cur = conn.cursor()
    cur.execute('SELECT * FROM sessions WHERE id = ?', (session_id,))
    row = cur.fetchone()
    conn.close()
    return row


def get_messages(session_id: str):
    conn = db_connection()
    cur = conn.cursor()
    cur.execute(
        'SELECT role, text, created_at FROM messages WHERE session_id = ? ORDER BY id ASC',
        (session_id,),
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def add_message(session_id: str, role: str, text: str):
    conn = db_connection()
    cur = conn.cursor()
    cur.execute(
        'INSERT INTO messages(session_id, role, text, created_at) VALUES (?, ?, ?, ?)',
        (session_id, role, text, now_ts()),
    )
    if role == 'user':
        cur.execute(
            'UPDATE sessions SET total_user_messages = total_user_messages + 1 WHERE id = ?',
            (session_id,),
        )
    conn.commit()
    conn.close()


def validate_access_code(code: str):
    conn = db_connection()
    cur = conn.cursor()
    cur.execute('SELECT * FROM access_codes WHERE code = ?', (code,))
    row = cur.fetchone()
    conn.close()
    if not row:
        return False, 'Неверный код доступа.'
    if not row['is_active']:
        return False, 'Код доступа отключён.'
    if row['used_count'] >= row['max_uses']:
        return False, 'Лимит использований для этого кода исчерпан.'
    return True, ''


def increment_code_usage(code: str):
    conn = db_connection()
    cur = conn.cursor()
    cur.execute('UPDATE access_codes SET used_count = used_count + 1 WHERE code = ?', (code,))
    conn.commit()
    conn.close()


def build_role_instructions(profile: dict) -> str:
    return f'''
Ты играешь роль собеседника в учебном тренажёре для руководителей.

Твоя скрытая роль: {profile['label']}.
Краткое описание: {profile['description']}.
Стиль общения: {profile['style']}.
Что тебе нравится в общении: {profile['likes']}.
Что тебя раздражает: {profile['dislikes']}.
Цель собеседника: {profile['goal']}.

Правила:
1. Никогда не называй свой типаж прямо.
2. Отвечай естественно, как живой человек.
3. Обычно отвечай в 1-4 предложениях.
4. Не превращай ответ в лекцию.
5. Сохраняй характер и манеру речи до конца диалога.
6. Если тебе задают хороший вопрос, можешь слегка раскрыться.
7. Если собеседник явно выбирает неуместный тон, реагируй в соответствии со своим типажом.
8. Общайся на русском языке.
'''.strip()


def build_conversation_input(profile: dict, history_rows):
    history_text = '\n'.join([f"{row['role'].upper()}: {row['text']}" for row in history_rows])
    return f"{build_role_instructions(profile)}\n\nИстория диалога:\n{history_text}\n\nПродолжи диалог одной следующей репликой от лица собеседника."


def generate_ai_reply(profile: dict, history_rows, session_id: str):
    if client is None:
        raise RuntimeError('OPENAI_API_KEY не задан в окружении сервера.')

    messages = [
        {"role": "system", "content": build_role_instructions(profile)}
    ]

    for row in history_rows:
        messages.append({
            "role": row["role"],
            "content": row["text"]
        })

    response = client.chat.completions.create(
        model=MODEL_NAME,
        messages=messages,
        temperature=0.7
    )

    return response.choices[0].message.content.strip()

    if text:
        return text.strip()

    # fallback in case SDK version formats text differently
    try:
        pieces = []
        for item in response.output:
            for content in item.content:
                if getattr(content, 'type', '') == 'output_text':
                    pieces.append(content.text)
        return '\n'.join(pieces).strip()
    except Exception as exc:
        raise RuntimeError(f'Не удалось извлечь текст ответа модели: {exc}')


def build_evaluation_prompt(session_row, profile: dict, history_rows, guessed_type: str):
    dialogue_text = '\n'.join([f"{row['role'].upper()}: {row['text']}" for row in history_rows])
    return f'''
Ты оцениваешь учебный диалог руководителя с виртуальным собеседником.

Скрытый типаж собеседника:
- label: {profile['label']}
- description: {profile['description']}
- style: {profile['style']}
- likes: {profile['likes']}
- dislikes: {profile['dislikes']}
- goal: {profile['goal']}

Гипотеза участника о типаже: {guessed_type or 'не указана'}

Диалог:
{dialogue_text}

Верни JSON со следующими полями:
- score_total: целое число от 0 до 100
- summary: 2-4 предложения
- strengths: массив из 3 коротких пунктов
- improvements: массив из 3 коротких пунктов

Оценивай по смыслу, а не формально. Не добавляй никаких полей сверх этих.
'''.strip()


def evaluate_session(session_row, profile: dict, guessed_type: str):
    if client is None:
        raise RuntimeError('OPENAI_API_KEY не задан в окружении сервера.')

    history_rows = get_messages(session_row['id'])

    response = client.chat.completions.create(
        model=MODEL_NAME,
        messages=[
            {"role": "system", "content": "Ты возвращаешь строго JSON."},
            {"role": "user", "content": build_evaluation_prompt(session_row, profile, history_rows, guessed_type)}
        ],
        temperature=0.3
    )

    raw_text = response.choices[0].message.content.strip()

    cleaned = raw_text
    if cleaned.startswith('```'):
        cleaned = cleaned.strip('`')
        cleaned = cleaned.replace('json\n', '', 1).strip()

    data = json.loads(cleaned)
    return data, raw_text



@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/health')
def health():
    return jsonify({'ok': True, 'model': MODEL_NAME})


@app.route('/api/start', methods=['POST'])
def start_session():
    ip = get_ip()
    if not check_rate_limit(ip):
        return jsonify({'error': 'Слишком много запросов. Попробуйте чуть позже.'}), 429
    log_request(ip, 'start')

    data = request.get_json(force=True, silent=True) or {}
    participant_name = (data.get('participant_name') or '').strip()
    access_code = (data.get('access_code') or '').strip().upper()

    if not participant_name:
        return jsonify({'error': 'Введите имя или псевдоним.'}), 400
    if not access_code:
        return jsonify({'error': 'Введите код доступа.'}), 400

    is_valid, error_message = validate_access_code(access_code)
    if not is_valid:
        return jsonify({'error': error_message}), 403

    profiles = load_profiles()
    profile = random.choice(profiles)
    session_id = str(uuid.uuid4())

    conn = db_connection()
    cur = conn.cursor()
    cur.execute(
        '''
        INSERT INTO sessions(id, participant_name, access_code, profile_id, profile_label, status, created_at, ip_address)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''',
        (
            session_id,
            participant_name,
            access_code,
            profile['id'],
            profile['label'],
            'active',
            now_ts(),
            ip,
        ),
    )
    conn.commit()
    conn.close()

    increment_code_usage(access_code)

    opening_line = profile.get('opening_line') or 'Здравствуйте. Давайте сразу к делу.'
    add_message(session_id, 'assistant', opening_line)

    return jsonify(
        {
            'session_id': session_id,
            'message': opening_line,
            'max_user_messages': MAX_USER_MESSAGES,
        }
    )


@app.route('/api/message', methods=['POST'])
def send_message():
    ip = get_ip()
    if not check_rate_limit(ip):
        return jsonify({'error': 'Слишком много запросов. Попробуйте чуть позже.'}), 429
    log_request(ip, 'message')

    data = request.get_json(force=True, silent=True) or {}
    session_id = (data.get('session_id') or '').strip()
    user_text = (data.get('message') or '').strip()

    if not session_id or not user_text:
        return jsonify({'error': 'Нужны session_id и message.'}), 400
    if len(user_text) > MAX_MESSAGE_CHARS:
        return jsonify({'error': f'Сообщение слишком длинное. Максимум {MAX_MESSAGE_CHARS} символов.'}), 400

    session_row = get_session(session_id)
    if not session_row:
        return jsonify({'error': 'Сессия не найдена.'}), 404
    if session_row['status'] != 'active':
        return jsonify({'error': 'Сессия уже завершена.'}), 400
    if session_row['total_user_messages'] >= MAX_USER_MESSAGES:
        return jsonify({'error': 'Лимит сообщений для этой сессии достигнут. Нажмите «Завершить».'}), 400

    add_message(session_id, 'user', user_text)

    profiles = load_profiles()
    profile = next((p for p in profiles if p['id'] == session_row['profile_id']), None)
    if not profile:
        return jsonify({'error': 'Профиль собеседника не найден.'}), 500

    history_rows = get_messages(session_id)
    try:
        reply = generate_ai_reply(profile, history_rows, session_id)
	except Exception as exc:
   	 print("AI ERROR:", exc)
   	 return jsonify({'error': str(exc)})

    add_message(session_id, 'assistant', reply)

    updated_session = get_session(session_id)
    remaining = max(0, MAX_USER_MESSAGES - updated_session['total_user_messages'])
    return jsonify({'reply': reply, 'remaining_messages': remaining})


@app.route('/api/finish', methods=['POST'])
def finish_session():
    ip = get_ip()
    if not check_rate_limit(ip):
        return jsonify({'error': 'Слишком много запросов. Попробуйте чуть позже.'}), 429
    log_request(ip, 'finish')

    data = request.get_json(force=True, silent=True) or {}
    session_id = (data.get('session_id') or '').strip()
    guessed_type = (data.get('guessed_type') or '').strip()

    if not session_id:
        return jsonify({'error': 'Нужен session_id.'}), 400

    session_row = get_session(session_id)
    if not session_row:
        return jsonify({'error': 'Сессия не найдена.'}), 404

    profiles = load_profiles()
    profile = next((p for p in profiles if p['id'] == session_row['profile_id']), None)
    if not profile:
        return jsonify({'error': 'Профиль собеседника не найден.'}), 500

    try:
        evaluation, raw_text = evaluate_session(session_row, profile, guessed_type)
    except Exception as exc:
        return jsonify({'error': f'Ошибка при оценивании диалога: {exc}'}), 500

    conn = db_connection()
    cur = conn.cursor()
    cur.execute(
        'UPDATE sessions SET status = ?, finished_at = ?, guessed_type = ? WHERE id = ?',
        ('finished', now_ts(), guessed_type, session_id),
    )
    cur.execute(
        '''
        INSERT OR REPLACE INTO evaluations(session_id, score_total, strengths, improvements, summary, raw_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ''',
        (
            session_id,
            int(evaluation.get('score_total', 0)),
            json.dumps(evaluation.get('strengths', []), ensure_ascii=False),
            json.dumps(evaluation.get('improvements', []), ensure_ascii=False),
            evaluation.get('summary', ''),
            raw_text,
            now_ts(),
        ),
    )
    conn.commit()
    conn.close()

    return jsonify(evaluation)


@app.route('/api/admin/sessions')
def admin_sessions():
    secret = request.args.get('secret', '')
    if secret != APP_SECRET:
        return jsonify({'error': 'Unauthorized'}), 401

    conn = db_connection()
    cur = conn.cursor()
    cur.execute(
        '''
        SELECT s.id, s.participant_name, s.access_code, s.profile_label, s.status,
               s.created_at, s.finished_at, e.score_total, e.summary
        FROM sessions s
        LEFT JOIN evaluations e ON e.session_id = s.id
        ORDER BY s.created_at DESC
        LIMIT 100
        '''
    )
    rows = [dict(row) for row in cur.fetchall()]
    conn.close()
    return jsonify({'sessions': rows})


@app.route('/api/admin/session/<session_id>')
def admin_session_detail(session_id):
    secret = request.args.get('secret', '')
    if secret != APP_SECRET:
        return jsonify({'error': 'Unauthorized'}), 401

    session_row = get_session(session_id)
    if not session_row:
        return jsonify({'error': 'Not found'}), 404

    conn = db_connection()
    cur = conn.cursor()
    cur.execute('SELECT * FROM evaluations WHERE session_id = ?', (session_id,))
    evaluation = cur.fetchone()
    conn.close()

    return jsonify(
        {
            'session': dict(session_row),
            'messages': [dict(row) for row in get_messages(session_id)],
            'evaluation': dict(evaluation) if evaluation else None,
        }
    )


if __name__ == '__main__':
    init_db()
    app.run(debug=True)
else:
    init_db()
