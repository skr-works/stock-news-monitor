import smtplib
import imaplib
from email.mime.text import MIMEText
from src.config import GMAIL_USER, GMAIL_APP_PASSWORD, EMAIL_TO
from datetime import datetime

def send_and_clean_email(subject, body):
    """メールを送信し、直後に送信済みトレイから削除（ゴミ箱へ移動）する"""
    
    # 1. メール送信 (SMTP)
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
        
        # 2. 送信履歴の削除 (IMAP)
        # 送信直後に行うことで、あなたの送信箱を汚しません
        cleanup_sent_mail(subject)
        
    except Exception as e:
        print(f"メール送信または削除エラー: {e}")

def cleanup_sent_mail(subject_keyword):
    """送信済みトレイから特定の件名のメールを探してゴミ箱に入れる"""
    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com")
        mail.login(GMAIL_USER, GMAIL_APP_PASSWORD)
        
        # Gmailの[Sent Mail]を選択
        # 環境によってフォルダ名が "[Gmail]/Sent Mail" または "[Gmail]/送信済みメール" の場合がある
        # 英語設定推奨だが、try-exceptで両方試す
        try:
            mail.select('"[Gmail]/Sent Mail"')
        except:
            mail.select('"[Gmail]/送信済みメール"')
            
        # 件名で検索 (文字コード対策でUTF-8指定)
        typ, data = mail.search("utf-8", f'(SUBJECT "{subject_keyword}")')
        
        for num in data[0].split():
            # ゴミ箱へ移動 (Gmail独自の拡張機能 X-GM-LABELS を使用)
            # \Trash ラベルを付けることでゴミ箱に入る
            mail.store(num, '+X-GM-LABELS', '\\Trash')
            print("送信履歴をゴミ箱に移動しました。")
            
        mail.close()
        mail.logout()
        
    except Exception as e:
        print(f"IMAP Cleanup Error: {e}")

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
        body += "-" * 20 + "\n"
        
    body += "\n※自動配信"
    return body
