import os
import json
import smtplib
import imaplib
import time
from datetime import datetime, timedelta
from email.mime.text import MIMEText

import pytz
import yfinance as yf
import gspread
import google.generativeai as genai
from oauth2client.service_account import ServiceAccountCredentials

# ==========================================
# 1. 設定 & 環境変数読み込み (Configuration)
# ==========================================

# GitHub Secrets等で "APP_SECRETS" という1つのJSON文字列にまとめて渡す想定
# 読み込めない場合は空の辞書を使用
env_secrets = os.environ.get("APP_SECRETS", "{}")
try:
    SECRETS = json.loads(env_secrets)
except json.JSONDecodeError:
    print("Error: Failed to parse APP_SECRETS. Check JSON format.")
    SECRETS = {}

# 設定値の展開
# GCP_SA_KEYはJSONオブジェクトそのものを期待
GCP_SA_KEY = SECRETS.get("GCP_SA_KEY", {}) 
SPREADSHEET_ID = SECRETS.get("SPREADSHEET_ID")
GEMINI_API_KEY = SECRETS.get("GEMINI_API_KEY")
GMAIL_USER = SECRETS.get("GMAIL_USER")
GMAIL_APP_PASSWORD = SECRETS.get("GMAIL_APP_PASSWORD")
EMAIL_TO = SECRETS.get("EMAIL_TO")

# 固定設定
SHEET_NAME = "保有銘柄2512"
# ノイズ除去用キーワード
IGNORE_KEYWORDS = ["PR TIMES", "キャンペーン", "開催", "お知らせ", "募集", "オープン", "記念", "発売"]
# 悪材料・好材料のキーワード候補（一次選別用）
BAD_KEYWORDS = ["下方修正", "減益", "赤字", "損失", "暴落", "ストップ安", "提訴", "訴訟", "疑義", "監理", "廃止", "売却", "不祥事", "不正", "リコール"]
GOOD_KEYWORDS = ["上方修正", "増益", "復配", "増配", "自社株買い", "株式分割", "提携", "買収", "ストップ高", "最高益", "黒字化", "承認"]

# ==========================================
# 2. スプレッドシート操作 (Sheet Loader)
# ==========================================

def get_stock_list():
    """スプレッドシートから銘柄コードのリストを取得する"""
    try:
        scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
        # 辞書型(GCP_SA_KEY)を直接使用
        creds = ServiceAccountCredentials.from_json_keyfile_dict(GCP_SA_KEY, scope)
        client = gspread.authorize(creds)
        
        sheet = client.open_by_key(SPREADSHEET_ID).worksheet(SHEET_NAME)
        
        # A2からA列の最後までの値を取得
        raw_values = sheet.col_values(1)[1:] # 1行目(ヘッダー)をスキップ
        
        stock_list = []
        for code in raw_values:
            code = str(code).strip()
            if not code:
                continue
            # 日本株コード（数字4桁）を想定し、末尾に.Tがなければ付与する
            if not code.endswith(".T"):
                code = f"{code}.T"
            stock_list.append(code)
            
        return stock_list
    except Exception as e:
        print(f"Error loading spreadsheet: {e}")
        return []

# ==========================================
# 3. ニュース取得 (News Fetcher)
# ==========================================

def get_target_time_range():
    """現在のJST時刻に基づいて、取得すべきニュースの時間範囲を返す"""
    jst = pytz.timezone('Asia/Tokyo')
    now = datetime.now(jst)
    
    # 12:05起動の回 (前日17:00 〜 当日12:04:59)
    if 11 <= now.hour <= 13:
        end_dt = now.replace(hour=12, minute=4, second=59)
        start_dt = (now - timedelta(days=1)).replace(hour=17, minute=0, second=0)
        mode = "NOON_CHECK"
        
    # 17:00起動の回 (当日12:05 〜 当日16:59:59)
    elif 16 <= now.hour <= 18:
        start_dt = now.replace(hour=12, minute=5, second=0)
        end_dt = now.replace(hour=16, minute=59, second=59)
        mode = "EVENING_CHECK"
        
    else:
        # 手動実行などで時間がずれた場合の安全策（直近6時間）
        end_dt = now
        start_dt = now - timedelta(hours=6)
        mode = "MANUAL_CHECK"
        
    return start_dt, end_dt, mode

def fetch_stock_news(tickers):
    """yfinanceでニュースを一括取得し、時間とキーワードでフィルタする"""
    start_dt, end_dt, mode = get_target_time_range()
    print(f"[{mode}] Time Filter: {start_dt} ~ {end_dt} (JST)")
    
    if not tickers:
        return []

    # Tickerオブジェクトを一括作成
    stocks = yf.Tickers(" ".join(tickers))
    
    candidates = []

    for ticker in tickers:
        try:
            # yfinanceのnews取得
            info = stocks.tickers[ticker].news
            
            for item in info:
                # タイムスタンプ判定 (Unix Time -> JST datetime)
                pub_time = datetime.fromtimestamp(item['providerPublishTime'], pytz.timezone('Asia/Tokyo'))
                
                # 1. 時間フィルタ
                if not (start_dt <= pub_time <= end_dt):
                    continue
                
                title = item['title']
                
                # 2. ノイズフィルタ
                if any(k in title for k in IGNORE_KEYWORDS):
                    continue
                
                # 3. 候補判定
                is_bad = any(k in title for k in BAD_KEYWORDS)
                is_good = any(k in title for k in GOOD_KEYWORDS)
                
                if is_bad or is_good:
                    candidates.append({
                        "ticker": ticker,
                        "title": title,
                        "time": pub_time.strftime('%m/%d %H:%M'),
                        "link": item['link'],
                        "type": "BAD" if is_bad else "GOOD" # とりあえずキーワードで仮分類
                    })
                    
        except Exception as e:
            # 個別の取得エラーは無視して次へ
            continue

    return candidates

# ==========================================
# 4. AI判定 (AI Judge)
# ==========================================

def judge_news_with_gemini(news_list):
    """ニュースリストをAIに渡し、本当に重要なものだけをフィルタリングする"""
    if not news_list:
        return [], []

    genai.configure(api_key=GEMINI_API_KEY)
    
    # モデル更新: Gemini 2.5 Flash
    # 高速かつバランスの取れた最新モデル
    model = genai.GenerativeModel('gemini-2.5-flash')
    
    bad_news_confirmed = []
    good_news_confirmed = []

    # AIへのリクエスト作成（バッチ処理）
    chunk_size = 10
    for i in range(0, len(news_list), chunk_size):
        chunk = news_list[i:i + chunk_size]
        
        prompt = "あなたはプロの機関投資家です。以下の日本株ニュースについて判定してください。\n\n"
        for idx, news in enumerate(chunk):
            prompt += f"No.{idx} [銘柄:{news['ticker']}] タイトル: {news['title']}\n"
        
        prompt += """
        \n【指示】
        各ニュースについて、以下の基準で判定し、結果のみを回答してください。
        
        ・株価が暴落する致命的な悪材料なら「BAD」
        ・株価が暴騰する強い好材料（福音）なら「GOOD」
        ・どちらでもない、または影響が軽微なら「IGNORE」
        
        回答フォーマット:
        No.0: BAD
        No.1: IGNORE
        ...
        """
        
        try:
            response = model.generate_content(prompt)
            lines = response.text.strip().split('\n')
            
            for line in lines:
                if "BAD" in line:
                    parts = line.split(':')
                    if len(parts) > 0:
                        idx_str = parts[0].replace('No.', '').strip()
                        if idx_str.isdigit():
                            idx = int(idx_str)
                            if idx < len(chunk):
                                bad_news_confirmed.append(chunk[idx])
                elif "GOOD" in line:
                    parts = line.split(':')
                    if len(parts) > 0:
                        idx_str = parts[0].replace('No.', '').strip()
                        if idx_str.isdigit():
                            idx = int(idx_str)
                            if idx < len(chunk):
                                good_news_confirmed.append(chunk[idx])
                        
        except Exception as e:
            print(f"AI API Error: {e}")
            continue

    return bad_news_confirmed, good_news_confirmed

# ==========================================
# 5. メール送信 (Mail Handler)
# ==========================================

def create_body(news_list, title_prefix):
    """メール本文を作成する"""
    if not news_list:
        return None
        
    body = "株式暴騰暴落ニュース監視システムです。\n"
    if title_prefix == "警告":
        body += "保有銘柄（日本株）に暴落リスクのある悪材料を検知しました。\n\n"
    else:
        body += "保有銘柄（日本株）に福音（好材料）を検知しました。\n\n"
        
    for i, news in enumerate(news_list, 1):
        body += f"{i}. [{news['ticker']}] \n"
        body += f"【時刻】 {news['time']}\n"
        body += f"【ニュース】 {news['title']}\n"
        body += f"【リンク】 {news['link']}\n"
        body += "-" * 20 + "\n"
        
    body += "\n※自動配信"
    return body

def cleanup_sent_mail(subject_keyword):
    """送信済みトレイから特定の件名のメールを探してゴミ箱に入れる"""
    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com")
        mail.login(GMAIL_USER, GMAIL_APP_PASSWORD)
        
        # フォルダ選択
        try:
            mail.select('"[Gmail]/Sent Mail"')
        except:
            mail.select('"[Gmail]/送信済みメール"')
            
        # 件名で検索
        typ, data = mail.search("utf-8", f'(SUBJECT "{subject_keyword}")')
        
        if data[0]:
            for num in data[0].split():
                mail.store(num, '+X-GM-LABELS', '\\Trash')
                print("送信履歴をゴミ箱に移動しました。")
            
        mail.close()
        mail.logout()
        
    except Exception as e:
        print(f"IMAP Cleanup Error: {e}")

def send_and_clean_email(subject, body):
    """メールを送信し、直後に送信済みトレイから削除する"""
    if not body:
        return

    msg = MIMEText(body)
    msg['Subject'] = subject
    msg['From'] = GMAIL_USER
    msg['To'] = EMAIL_TO
    
    try:
        # 送信処理
        server = smtplib.SMTP_SSL("smtp.gmail.com", 465)
        server.login(GMAIL_USER, GMAIL_APP_PASSWORD)
        server.send_message(msg)
        server.quit()
        print(f"メール送信成功: {subject}")
        
        # 送信履歴の削除
        cleanup_sent_mail(subject)
        
    except Exception as e:
        print(f"メール送信または削除エラー: {e}")

# ==========================================
# 6. メイン処理 (Main)
# ==========================================

def main():
    print("=== System Start ===")
    
    # 1. 銘柄読み込み
    tickers = get_stock_list()
    print(f"監視対象: {len(tickers)} 銘柄")
    
    if not tickers:
        print("銘柄リストが取得できませんでした。Secretsの設定を確認してください。終了します。")
        return

    # 2. ニュース収集 & フィルタリング
    candidates = fetch_stock_news(tickers)
    print(f"一次候補ニュース: {len(candidates)} 件")
    
    if not candidates:
        print("対象期間の重要ニュースはありませんでした。")
        return

    # 3. AI判定 (Gemini 2.5 Flash)
    bad_news, good_news = judge_news_with_gemini(candidates)
    
    now_str = datetime.now().strftime('%m/%d %H:%M')

    # 4. メール送信（悪材料）
    if bad_news:
        subject = f"【警告】保有株に悪材料検知 ({len(bad_news)}件) - {now_str}"
        body = create_body(bad_news, "警告")
        send_and_clean_email(subject, body)
    else:
        print("悪材料なし")

    # 5. メール送信（好材料）
    if good_news:
        subject = f"【福音】保有株に好材料検知 ({len(good_news)}件) - {now_str}"
        body = create_body(good_news, "福音")
        send_and_clean_email(subject, body)
    else:
        print("好材料なし")

    print("=== System End ===")

if __name__ == "__main__":
    main()
