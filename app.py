from flask import Flask, jsonify, request, render_template, session, redirect, url_for
from urllib.parse import urlparse, urljoin
from functools import wraps
import json
import math
import os
import urllib.request
from datetime import datetime, timedelta, timezone

# app.py はプロジェクト直下に置く。
# 実体（templates / static / data）は bousai_app/ 配下にあるので、そこを参照する。
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.join(BASE_DIR, 'bousai_app')

app = Flask(
    __name__,
    template_folder=os.path.join(APP_DIR, 'templates'),
    static_folder=os.path.join(APP_DIR, 'static'),
)
app.secret_key = 'your-secret-key-here'

@app.context_processor
def navigation_context():
    """全画面共通ナビゲーションの現在画面情報を提供する"""
    page_details = {
        'index': ('ホーム', 'home'),
        'shelter_search': ('避難所検索', 'shelter_search'),
        'search_results': ('検索結果', 'shelter_search'),
        'all_shelters': ('避難所一覧', 'shelter_search'),
        'shelter_register': ('避難所登録', 'shelter_register'),
        'board': ('指示ボード', 'board'),
        'login': ('ログイン', 'login'),
    }
    return {
        'current_page': page_details.get(request.endpoint, ('防災アプリ', '')),
        'active_nav_item': page_details.get(request.endpoint, ('', ''))[1],
    }

# 管理者認証情報
ADMIN_CREDENTIALS = {
    'admin': '123'
}

# ────────────────────────────────
# 気象警報・注意報設定
PREFECTURE_CODE = "020000"  # 青森県
AREA_NAME = "青森市"

# PDF記載の市町村コード220100に対応する警報JSONのareaCode（7桁形式）
AREA_CODE = "0220100"

WARNING_URL = (
    f"https://www.jma.go.jp/bosai/warning/data/r8/{PREFECTURE_CODE}.json"
)

JST = timezone(timedelta(hours=9))

# 警報・注意報のコード一覧
WARNING_CODES = {
    "00": "解除",
    "02": "暴風雪警報",
    "03": "レベル3大雨警報",
    "04": "洪水警報",
    "05": "暴風警報",
    "06": "大雪警報",
    "07": "波浪警報",
    "08": "レベル3高潮警報",
    "09": "レベル3土砂災害警報",
    "10": "レベル2大雨注意報",
    "12": "大雪注意報",
    "13": "風雪注意報",
    "14": "雷注意報",
    "15": "強風注意報",
    "16": "波浪注意報",
    "17": "融雪注意報",
    "18": "洪水注意報",
    "19": "レベル2高潮注意報",
    "20": "濃霧注意報",
    "21": "乾燥注意報",
    "22": "なだれ注意報",
    "23": "低温注意報",
    "24": "霜注意報",
    "25": "着氷注意報",
    "26": "着雪注意報",
    "27": "その他の注意報",
    "29": "レベル2土砂災害注意報",
    "32": "暴風雪特別警報",
    "33": "レベル5大雨特別警報",
    "35": "暴風特別警報",
    "36": "大雪特別警報",
    "37": "波浪特別警報",
    "38": "レベル5高潮特別警報",
    "39": "レベル5土砂災害特別警報",
    "43": "レベル4大雨危険警報",
    "48": "レベル4高潮危険警報",
    "49": "レベル4土砂災害危険警報"
}

# ────────────────────────────────
# サンプルデータの読み込み
DATA_FILE = os.path.join(APP_DIR, 'data', 'shelters.json')
INSTRUCTIONS_FILE = os.path.join(APP_DIR, 'data', 'instructions.json')

def load_json(path, default):
    """JSONファイルを読み込む（存在しない・壊れている場合は default を返す）"""
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default

shelters = load_json(DATA_FILE, [])
instructions = load_json(INSTRUCTIONS_FILE, [])

def save_instructions():
    """指示ボードのデータをファイルに保存する"""
    with open(INSTRUCTIONS_FILE, 'w', encoding='utf-8') as f:
        json.dump(instructions, f, ensure_ascii=False, indent=2)

def save_shelters():
    """避難所データをファイルに保存する"""
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(shelters, f, ensure_ascii=False, indent=2)
# ────────────────────────────────

# ────────────────────────────────
# 認証関連の設定とヘルパー関数
def is_safe_url(target):
    """リダイレクト先URLが安全かどうかチェック"""
    ref_url = urlparse(request.host_url)
    test_url = urlparse(urljoin(request.host_url, target))
    return test_url.scheme in ('http', 'https') and ref_url.netloc == test_url.netloc

def login_required(f):
    """認証が必要なページに付けるデコレータ"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('logged_in'):
            # 現在のURLをnextパラメータとしてログイン画面にリダイレクト
            return redirect(url_for('login', next=request.url))
        return f(*args, **kwargs)
    return decorated_function

def get_japan_time():
    """日本時間（JST）の現在時刻を取得する"""
    return datetime.now(JST).strftime("%Y年%m月%d日 %H:%M")

def parse_japan_time(value):
    """保存済みの日本語日時を時系列ソート用の UNIX 時刻に変換する"""
    try:
        return datetime.strptime(value, "%Y年%m月%d日 %H:%M").replace(tzinfo=JST).timestamp()
    except (TypeError, ValueError):
        return 0


def format_report_time(iso_str):
    """気象庁の発表時刻（ISO形式）をJSTの表示用文字列に変換する"""
    if not iso_str:
        return "不明"
    try:
        parsed = datetime.fromisoformat(iso_str.replace('Z', '+00:00'))
        if parsed.tzinfo:
            parsed = parsed.astimezone(JST)
        return parsed.strftime("%Y年%m月%d日 %H:%M")
    except ValueError:
        return iso_str


def filter_shelters(district=None):
    """district 指定があれば一致する避難所のみ、なければ全件を返す"""
    return [s for s in shelters if not district or s.get('district') == district]

def is_public_instruction(instruction):
    """住民・観光客向けの指示かを判定する（旧データの「住民」も含む）"""
    return instruction.get('target') in ('住民', '住民・観光客')

def get_public_emergency_notices():
    """ホーム画面に表示する、未解除の住民・観光客向け指示を返す"""
    notices = [
        {
            'content': instruction.get('content', '').strip(),
            'priority': instruction.get('priority') if instruction.get('priority') == 'high' else 'normal',
            'received_at': instruction.get('created_at', '日時不明'),
            'shelter': instruction.get('shelter', '').strip(),
        }
        for instruction in instructions
        if is_public_instruction(instruction)
        and instruction.get('content', '').strip()
        and instruction.get('status') not in ('解除', '完了')
    ]
    notices.sort(
        key=lambda notice: parse_japan_time(notice['received_at']),
        reverse=True,
    )
    return notices


def parse_area_warnings(warning_data):
    """気象庁の新形式JSONから対象市区町村の発表・継続中の情報を抽出する"""
    if not isinstance(warning_data, list):
        raise ValueError("気象庁の警報・注意報データが新形式の配列ではありません")

    latest_area = None
    latest_report_datetime = ""

    for report in warning_data:
        if not isinstance(report, dict):
            continue

        report_datetime = report.get("reportDatetime")
        if not isinstance(report_datetime, str) or report_datetime <= latest_report_datetime:
            continue

        warning = report.get("warning")
        if not isinstance(warning, dict):
            continue

        class20_items = warning.get("class20Items", [])
        if not isinstance(class20_items, list):
            continue

        area = next(
            (
                item for item in class20_items
                if isinstance(item, dict)
                and item.get("areaCode") == AREA_CODE
            ),
            None
        )
        if area:
            latest_area = area
            latest_report_datetime = report_datetime

    warnings = []
    if isinstance(latest_area, dict):
        kinds = latest_area.get("kinds", [])
        if isinstance(kinds, list):
            for kind in kinds:
                if not isinstance(kind, dict):
                    continue

                status = kind.get("status", "")
                code = kind.get("code", "")
                if status not in ("発表", "継続", "解除") or not code:
                    continue

                warnings.append({
                    "name": WARNING_CODES.get(
                        code,
                        f"不明な警報・注意報 (コード: {code})"
                    ),
                    "code": code,
                    "status": status
                })

    return warnings, latest_report_datetime


def get_weather_warnings():
    """対象市区町村の警報・注意報を取得する"""
    try:
        # 青森県の新形式（令和8年～）警報・注意報データを取得
        with urllib.request.urlopen(url=WARNING_URL, timeout=10) as res:
            warning_data = json.loads(res.read())

        warnings, report_datetime = parse_area_warnings(warning_data)

        return {
            "area_name": AREA_NAME,
            "warnings": warnings,
            "report_time": format_report_time(report_datetime),
            "last_fetch_time": get_japan_time()
        }

    except Exception:
        return {
            "area_name": AREA_NAME,
            "warnings": [],
            "report_time": "取得失敗",
            "last_fetch_time": get_japan_time(),
            "error": True
        }


# トップページ：templates/index.html を返す（住民向け指示も表示する）
@app.route('/')
def index():
    resident_notices = [i for i in instructions if is_public_instruction(i)]
    emergency_notices = get_public_emergency_notices()
    disaster_information = []
    for instruction in instructions:
        content = instruction.get('content', '').strip()
        if not content:
            continue

        target = instruction.get('target', '').strip()
        received_at = instruction.get('created_at', '日時不明')
        details = []
        if target:
            details.append(f'通知先: {target}')
        if instruction.get('shelter'):
            details.append(f'避難先: {instruction["shelter"]}')

        disaster_information.append({
            'type': '住民向け発信' if is_public_instruction(instruction) else '庁内指示',
            'content': content,
            'details': ' / '.join(details),
            'received_at': received_at,
            'sort_key': parse_japan_time(received_at),
        })

    disaster_information.sort(key=lambda item: item['sort_key'], reverse=True)
    map_shelters = [
        {
            'name': shelter['name'],
            'latitude': shelter['latitude'],
            'longitude': shelter['longitude'],
            'status': 'open' if shelter.get('status') == 'open' else 'closed',
        }
        for shelter in shelters
        if isinstance(shelter.get('name'), str)
        and isinstance(shelter.get('latitude'), (int, float))
        and not isinstance(shelter.get('latitude'), bool)
        and math.isfinite(shelter['latitude'])
        and -90 <= shelter['latitude'] <= 90
        and isinstance(shelter.get('longitude'), (int, float))
        and not isinstance(shelter.get('longitude'), bool)
        and math.isfinite(shelter['longitude'])
        and -180 <= shelter['longitude'] <= 180
    ]
    return render_template(
        'index.html',
        resident_notices=resident_notices,
        emergency_notices=emergency_notices,
        disaster_information=disaster_information,
        map_shelters=map_shelters,
    )

# ログインページ
@app.route('/login', methods=['GET', 'POST'])
def login():
    # リダイレクト先を取得（デフォルトは避難所登録画面）
    next_url = request.args.get('next') or request.form.get('next')

    # 安全でないURLの場合はデフォルトページにリダイレクト
    if not next_url or not is_safe_url(next_url):
        next_url = url_for('shelter_register')

    if request.method == 'POST':
        password = request.form.get('password', '').strip()

        # 認証チェック
        username = next(
            (name for name, registered_password in ADMIN_CREDENTIALS.items()
             if registered_password == password),
            None
        )
        if username:
            session['logged_in'] = True
            session['username'] = username
            # ログイン成功後は指定されたページにリダイレクト
            return redirect(next_url)
        return render_template('login.html', error=True, message="パスワードが正しくありません。", next=next_url)

    # ログイン済みの場合は指定されたページにリダイレクト
    if session.get('logged_in'):
        return redirect(next_url)

    return render_template('login.html', next=next_url)

# ログアウト
@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

# 避難所登録ページ
@app.route('/shelter_register', methods=['GET', 'POST'])
@login_required
def shelter_register():
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        if not name:
            return render_template(
                'shelter_register.html',
                error=True,
                message='避難所名を入力してください'
            )

        status = request.form.get('status', 'closed')
        if status not in ('open', 'closed'):
            return render_template(
                'shelter_register.html',
                error=True,
                message='開設状況を選択してください'
            )

        latitude_value = request.form.get('latitude', '').strip()
        longitude_value = request.form.get('longitude', '').strip()
        if bool(latitude_value) != bool(longitude_value):
            return render_template(
                'shelter_register.html',
                error=True,
                message='地図に表示する場合は、緯度と経度を両方入力してください'
            )

        coordinates = {}
        if latitude_value and longitude_value:
            try:
                latitude = float(latitude_value)
                longitude = float(longitude_value)
            except ValueError:
                return render_template(
                    'shelter_register.html',
                    error=True,
                    message='緯度・経度は数値で入力してください'
                )

            if (
                not math.isfinite(latitude)
                or not math.isfinite(longitude)
                or not -90 <= latitude <= 90
                or not -180 <= longitude <= 180
            ):
                return render_template(
                    'shelter_register.html',
                    error=True,
                    message='緯度は-90〜90、経度は-180〜180の範囲で入力してください'
                )
            coordinates = {'latitude': latitude, 'longitude': longitude}

        shelter_id = max(
            (shelter.get('id', 0) for shelter in shelters),
            default=0
        ) + 1
        shelters.append({'id': shelter_id, 'name': name, 'status': status, **coordinates})
        save_shelters()
        return render_template(
            'shelter_register.html',
            success=True,
            message='避難所を登録しました'
        )

    return render_template('shelter_register.html')

# 避難所検索ページ
@app.route('/shelter_search')
def shelter_search():
    return render_template('shelter_search.html')

# 全施設一覧ページ
@app.route('/all_shelters')
def all_shelters():
    return render_template('search_results.html', results=shelters)


# 指示ボード：住民向けの指示を一覧で確認する
@app.route('/board', methods=['GET', 'POST'])
@login_required
def board():
    error_message = None
    success_message = None
    if request.method == 'POST':
        target = request.form.get('target', '').strip()
        content = request.form.get('content', '').strip()
        shelter = request.form.get('shelter', '').strip()
        priority = request.form.get('priority', 'normal')
        if target not in ('住民・観光客', '防災課', '道路管理課') or not content:
            error_message = '宛先を選択し、指示内容を入力してください。'
        elif priority not in ('high', 'normal'):
            error_message = '緊急度を選択してください。'
        else:
            instruction = {
                'id': max((item.get('id', 0) for item in instructions), default=0) + 1,
                'target': target,
                'content': content,
                'shelter': shelter,
                'priority': priority,
                'status': '発信中',
                'created_at': get_japan_time(),
                'updated_at': get_japan_time(),
            }
            instructions.append(instruction)
            try:
                save_instructions()
            except (OSError, TypeError):
                instructions.pop()
                app.logger.exception('指示の保存に失敗しました')
                error_message = '指示を保存できませんでした。時間をおいて再度お試しください。'
            else:
                success_message = '指示を発信しました。'

    board_instructions = sorted(
        instructions,
        key=lambda item: parse_japan_time(item.get('created_at', '')),
        reverse=True,
    )
    return render_template(
        'board.html',
        instructions=board_instructions,
        error_message=error_message,
        success_message=success_message,
    )

# JSON API：ホーム画面向けの住民・観光客向け指示
@app.route('/api/resident_instructions')
def api_resident_instructions():
    return jsonify(get_public_emergency_notices())

# 検索結果ページ：templates/search_results.html を返す
@app.route('/search_results')
def search_results():
    results = filter_shelters(request.args.get('district'))
    return render_template('search_results.html', results=results)

# JSON API：/shelters?district=地区名
@app.route('/shelters', methods=['GET'])
def get_shelters():
    results = filter_shelters(request.args.get('district'))

    if not results:
        # 見つからなければエラー JSON を返す
        return jsonify({'error': 'No shelters found'}), 404

    # 見つかったらリストを JSON で返す
    return jsonify(results)

# 気象警報・注意報API
@app.route('/api/weather_warnings')
def api_weather_warnings():
    """気象警報・注意報をJSON形式で返すAPI"""
    return jsonify(get_weather_warnings())

if __name__ == '__main__':
    app.run(debug=True, port=5000)