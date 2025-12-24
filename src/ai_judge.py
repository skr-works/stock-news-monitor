import google.generativeai as genai
from src.config import GEMINI_API_KEY

def judge_news_with_gemini(news_list):
    """ニュースリストをAIに渡し、本当に重要なものだけをフィルタリングする"""
    if not news_list:
        return [], []

    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel('gemini-1.5-flash')
    
    bad_news_confirmed = []
    good_news_confirmed = []

    # AIへのリクエスト作成（バッチ処理）
    # 5件ずつくらいにまとめて送るのが安全
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
                    idx = int(line.split(':')[0].replace('No.', ''))
                    if idx < len(chunk):
                        bad_news_confirmed.append(chunk[idx])
                elif "GOOD" in line:
                    idx = int(line.split(':')[0].replace('No.', ''))
                    if idx < len(chunk):
                        good_news_confirmed.append(chunk[idx])
                        
        except Exception as e:
            print(f"AI API Error: {e}")
            # エラー時は安全側に倒して、キーワード判定をそのまま採用してもよいが、今回はスキップ
            continue

    return bad_news_confirmed, good_news_confirmed
