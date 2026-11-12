# ニュース集約

**言語:** [简体中文](README.md) · [English](README.en.md) · 日本語

公開 RSS、RSSHub、GDELT、任意の NYT API から記事を収集し、ローカル画面と任意の AI 要約にまとめます。Feishu Webhook への配信は任意です。初期設定ではローカルホストでのみ公開します。

## クイックスタート

Python 3.11 以上と Docker が必要です。`.env.example` を `.env` にコピーし、`POSTGRES_PASSWORD` を設定します。`docker compose up -d` を実行し、`pip install -r requirements.txt` で依存関係を入れ、`python api/app.py` で起動してください。`http://127.0.0.1:8099` を開きます。定期収集は `python main.py` で開始します。初期値 `DRY_RUN=true` では外部配信を行いません。

ニュースソースは `config/sources.yaml` にあります。利用前に画面で個別にテストしてください。RSSHub の一部ルートには自分のインスタンスが必要です。OpenRouter、NYT、Feishu の機能には別途認証情報が必要です。ソースの稼働状況や利用条件は変わります。ローカルホスト以外に公開する場合は、先に認証とリバースプロキシを追加してください。

公開版には `.env`、ログ、データベースを含めていません。設定テストは通過していますが、実際のソース、PostgreSQL/Redis、配信は各環境で検証が必要です。MIT License。

## 言語

画面右下の選択メニューで中国語・英語・日本語のナビゲーション、フィルター、主な状態表示を切り替えられます。外部のタイトル、原文、診断の詳細は元の言語のまま表示します。
