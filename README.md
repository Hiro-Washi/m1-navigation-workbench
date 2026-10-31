# M1 Navigation Workbench

保存地図に地点・訪問順・巡回・進入禁止領域を登録し、PCに保存する**計画エディター単独候補**。全M1システムの移行ではない。リポジトリは `Hiro-Washi/m1-navigation-workbench`。Public公開は管理者承認済み。利用ライセンスは未指定。機体接続、ROS起動、走行実行、機体への自動配備は含まない。

## 初回起動

前提はPython 3.10以上（検証版は3.13）とNode.js 22。Python標準ライブラリとNode組込みのみで動き、pip/npm installは不要。NodeとPythonをPATHで利用可能にする。以下を候補ディレクトリで実行する。

```
node preview/planning_server.mjs
```

表示された `http://127.0.0.1:18800` を開く。同じPCのloopbackだけで待ち受ける。既存portと競合したら終了する。別portが必要なら起動前に環境変数 `M1_PLANNING_PORT` を設定する（例18801）。外部IPを指定する設定はない。

Pythonコマンドが `python` でない環境は `M1_PLANNING_PYTHON` に実行ファイルを指定する。保存先は `M1_NAV_PLANS_ROOT` で指定可能。未指定時はWindowsのLOCALAPPDATA、その他はホームの下の `M1Console/navigation-plans`。保存先はソースtree外の専用ディレクトリにする。認証・SSH設定・機体アドレスは不要。

環境変数の指定例（PowerShell）:

```powershell
$env:M1_PLANNING_PYTHON = 'python'
$env:M1_NAV_PLANS_ROOT = Join-Path $env:LOCALAPPDATA 'M1WorkbenchData'
node preview/planning_server.mjs
```

POSIX shellでは例えば `M1_PLANNING_PYTHON=python3 M1_NAV_PLANS_ROOT="$HOME/M1WorkbenchData" node preview/planning_server.mjs`。URLは `localhost` ではなく出力された `127.0.0.1` を使う（Host/Originを照合する）。終了は端末でCtrl+C。HTTP受付を閉じ、その起動が所有する保存workerへ終了要求する。OSによって終了signalの扱いが違うため、終了できない場合は端末のprocess状態を確認する。終了で保存データを削除しない。

## 使い方と保存

1. 保存済み地図のYAMLとPGMを明示選択し「地図を読み込む」。YAML内のimage pathは開かず、選んだPGMとhashで結び付ける。
2. 地点追加モードでクリック、向きはドラッグ。訪問順と巡回数・待機時間、進入禁止領域を編集する。
3. 「保存」でPCのmap原本と計画JSONを保存する。別画面が先に更新していた場合はrevision競合として拒否し、勝手に上書きしない。
4. JSONを書き出す場合は元のYAML/PGMも一緒に保管する。他PCではこの候補を用意し、同じYAML/PGMを先に読み込んでから計画JSONを読む。

地図は限定されたflat Nav2 map_saver YAML、trinary、8bit PGM P2/P5。画像は最大400万pixel、1辺4096pixelまで。任意YAML構文やPNG等は未対応。計画数・地点数等の上限はserviceが検証する。線は訪問順であって障害物回避経路ではない。進入禁止や速度上限は計画データに保存するだけで、実機へ適用しない。初期0.15m/s・0.5rad/sは安全性の実測保証ではない。画面のM1 Consoleリンクは「実機Console未同梱」の説明ページへ移る。

ブラウザーの見た目・操作性の実画面受入は今回未実施。HTTP保存一巡とモデル試験の成功を、Nav走行・地図精度・物理停止の受入へ拡張しない。保存原本と計画のバックアップは利用者が行う。

## CI・版・公開境界

```
python -B ci/run.py
```

source hash照合、Python schema/保存/競合/破損拒否、Node座標・連鎖削除、一時コピーとpip無しvenvからの実HTTP初回一巡を実施する。loopbackのみで、実機・SSH・CDはない。各工程90秒、GitHub Actions6分。Actions workflowは雛形でありGitHub runnerでの実行は別確認。第三者actionは公式commitの固定SHAで、Python/Node minorとrunner全体の完全固定までは行わない。

`source-manifest.json`は無改変コピーした実装と候補テンプレートのSHA256。起動時に全対象hashを照合し、ブラウザーstatic bytesは起動時に固定する。起動中はディレクトリを編集しない。変更時は元sourceから再exportし、manifestと差分を一緒にレビューする。hashは公開権・作者・安全性を証明しない。

メーカーcode、mesh、Bag、画像、私的evidence、接続情報は同梱しない。公開範囲はこの独立した計画エディター。LICENSEは未指定で、ライセンス付与は管理者が別途決める。今後の更新も具体diffと検証を確認してから公開する。未投稿のIssue案は `ISSUE_DRAFT.md`。
