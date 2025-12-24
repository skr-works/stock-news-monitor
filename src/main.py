from src.sheet_loader import get_stock_list
from src.news_fetcher import fetch_stock_news
from src.ai_judge import judge_news_with_gemini
from src.mail_handler import send_and_clean_email, create_body
from datetime import datetime

def main():
    print("=== System Start ===")
    
    # 1. 銘柄読み込み
    tickers = get_stock_list()
    print(f"監視対象: {len(tickers)} 銘柄")
    
    if not tickers:
        print("銘柄リストが取得できませんでした。終了します。")
        return

    # 2. ニュース収集 & フィルタリング
    candidates = fetch_stock_news(tickers)
    print(f"一次候補ニュース: {len(candidates)} 件")
    
    if not candidates:
        print("対象期間の重要ニュースはありませんでした。")
        return

    # 3. AI判定
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
