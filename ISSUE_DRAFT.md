# 草案：保存地図の計画エディターを独立利用可能なrepoとして整理する

未投稿。配置先はPublicのHiro-Washi/m1-navigation-workbench。リポジトリ公開は承認済みだが、このIssueの投稿は別途判断する。

目的は、他PCで機体に接続せず、地図を読み込み、地点・経路・巡回・禁止領域を保存／交換できる初回手順を確立すること。

範囲は明示allowlistの現行plannerとPython保存serviceの無改変コピー、loopback専用server、内容hash、README、非実機CI。全M1repo移行、Nav実行、SSH、ROS、Bag、メーカー資産、自動配備は含めない。

受入は、清潔なコピーと空venvから実HTTPでlist→地図取込／読取→計画作成→load/update→古いrevision拒否が成立すること。原点・回転付き座標、連鎖削除、破損・範囲外・別origin拒否、所有process終了を確認すること。実ブラウザー操作とアクセシビリティは別途未完として残す。

依存はコード権利とライセンス確認、repo管理者／公開範囲、既存Issue照合。担当はソース所有者とレビュー者を公開前に確定する。公開前に対象ファイル一覧・具体diff・検証・残制限を提示し、利用者の最終判断を得る。CI成功だけで実機CDを追加しない。
