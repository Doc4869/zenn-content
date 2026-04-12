import os
import random
import glob
import subprocess
import re
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Optional

import anthropic
from dotenv import load_dotenv

load_dotenv("/Users/m.ida/Desktop/obsidian-x-poster/.env")

OBSIDIAN_PATH = "/Users/m.ida/Library/Mobile Documents/iCloud~md~obsidian/Documents/井田/医学知識"
ARTICLES_PATH = "/Users/m.ida/Desktop/zenn-content/articles"
CLAUDE_MODEL = "claude-opus-4-5"


def load_random_md_file() -> Optional[dict]:
    """更新日時が新しい順に上位10件を取得し、ランダムに1件選ぶ"""
    md_files = glob.glob(os.path.join(OBSIDIAN_PATH, "**/*.md"), recursive=True)
    md_files += glob.glob(os.path.join(OBSIDIAN_PATH, "*.md"))
    md_files = list(set(md_files))

    if not md_files:
        return None

    md_files.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    pool = md_files[:10]
    selected_path = random.choice(pool)

    try:
        with open(selected_path, "r", encoding="utf-8") as f:
            content = f.read()
        return {
            "filename": Path(selected_path).stem,
            "content": content[:5000],
        }
    except Exception as e:
        print(f"読み込みエラー: {selected_path} - {e}")
        return None


def slugify(text: str) -> str:
    """ファイル名に使えるslugを生成する（ASCII部分を優先して抽出）"""
    text = unicodedata.normalize("NFKC", text)
    # ASCII英数字・ハイフン・スペースのみ残す
    ascii_only = re.sub(r"[^a-zA-Z0-9\s-]", " ", text)
    ascii_only = re.sub(r"[\s_-]+", "-", ascii_only).strip("-").lower()
    if ascii_only:
        return ascii_only[:50]
    # ASCII部分が取れない場合は時刻のみ（save_article側で日付プレフィックスがつく）
    return datetime.now().strftime("%H%M%S")


def extract_pmids(content: str) -> list[str]:
    """ObsidianのコンテンツからPMIDを文書出現順に抽出する（重複除去）"""
    # 「PMID: 12345678」形式 と 「##### 12345678」見出し形式の両方を一括マッチ
    pattern = re.compile(
        r"(?:PMID[:\s]*(\d{7,8}))|(?:^#{1,6}\s+(\d{7,8})\s*$)",
        re.IGNORECASE | re.MULTILINE,
    )
    seen: set[str] = set()
    unique: list[str] = []
    for m in pattern.finditer(content):
        p = m.group(1) or m.group(2)
        if p and p not in seen:
            seen.add(p)
            unique.append(p)
    return unique


def build_footnotes_instruction(pmids: list[str]) -> str:
    """プロンプトに埋め込む脚注指示文と参考文献セクションを生成する"""
    if not pmids:
        return ""

    numbered = "\n".join(f"[^{i+1}]: PMID: {p}" for i, p in enumerate(pmids))
    mapping = "\n".join(f"  - [^{i+1}] → PMID {p}" for i, p in enumerate(pmids))

    return f"""
【参考文献の脚注ルール】
ノートには以下のPMIDが含まれています。本文中で該当する内容に言及するときは \
[^番号] の形式で脚注番号を挿入してください。
全てのPMIDを使う必要はありませんが、根拠となる記述には積極的に付けてください。

番号とPMIDの対応：
{mapping}

記事末尾に以下の参考文献セクションをそのまま追加してください（番号・PMIDは変えないこと）：

## 参考文献

{numbered}
"""


def generate_article(file: dict) -> dict:
    """Claudeを使ってZenn記事を生成する"""
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    pmids = extract_pmids(file["content"])
    footnotes_instruction = build_footnotes_instruction(pmids)

    prompt = f"""以下は医師が書いた医学知識のノートです。

=== ファイル: {file['filename']} ===
{file['content']}

このノートを元に、Zenn向けの医学記事を1つ作成してください。

【記事の条件】
- 文字数：1000〜2000文字（本文のみ、frontmatterと参考文献セクションは除く）
- 対象読者：医療従事者〜医療に興味がある一般人
- 構成：導入 → 本題 → 臨床での気づき → まとめ
- 各セクションに ## の見出しをつける

【文体の条件】
- @neshige_s さんのXの文体を参考に、カジュアルフォーマル
- 体言止め・短文・独り言調を混ぜたテンポ感
- 説明しすぎない。読者を信頼する
- 「気づきの共有」であって「知識の披露」ではない
- 専門用語は使ってよいが、文脈で伝わるように
- AIっぽい説明調・箇条書き羅列を避ける
- 「〜ですよね」「〜と思います」で締めない
- 臨床での生の気づきや経験談を盛り込む
- 読んでいて「あ、わかる」と思えるような書き方
- 教科書的な羅列ではなく、現場の肌感覚で語る
{footnotes_instruction}
【出力フォーマット】
以下のZenn frontmatter付きmarkdownをそのまま出力してください。
他の説明文や前置きは不要です。

---
title: "（記事タイトル）"
emoji: "（テーマに合った絵文字1文字）"
type: "idea"
topics: ["（関連トピック1）", "（関連トピック2）", "（関連トピック3）"]
published: false
---

（本文）
{"（末尾に ## 参考文献 セクション）" if pmids else ""}"""

    message = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=3000,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = message.content[0].text.strip()

    # frontmatterからtitleを抽出
    title_match = re.search(r'^title:\s*["\'](.+?)["\']', raw, re.MULTILINE)
    title = title_match.group(1) if title_match else file["filename"]

    return {
        "title": title,
        "content": raw,
    }


def save_article(title: str, content: str) -> str:
    """articles/フォルダにmarkdownファイルとして保存する"""
    os.makedirs(ARTICLES_PATH, exist_ok=True)

    slug = slugify(title)
    date_str = datetime.now().strftime("%Y%m%d")
    filename = f"{date_str}-{slug}.md"
    filepath = os.path.join(ARTICLES_PATH, filename)

    # 同名ファイルが存在する場合は連番をつける
    counter = 1
    while os.path.exists(filepath):
        filename = f"{date_str}-{slug}-{counter}.md"
        filepath = os.path.join(ARTICLES_PATH, filename)
        counter += 1

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)

    print(f"✅ 記事を保存しました: {filepath}")
    return filepath


def git_push(filepath: str) -> None:
    """git add・commit・pushを実行する"""
    repo_dir = "/Users/m.ida/Desktop/zenn-content"
    filename = os.path.basename(filepath)

    commands = [
        ["git", "-C", repo_dir, "add", filepath],
        ["git", "-C", repo_dir, "commit", "-m", f"add: {filename}"],
        ["git", "-C", repo_dir, "push"],
    ]

    for cmd in commands:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"⚠️  コマンド失敗: {' '.join(cmd)}")
            print(result.stderr)
            raise RuntimeError(f"git操作に失敗しました: {result.stderr}")
        print(f"✅ {' '.join(cmd[2:])}")


def main():
    print("Obsidianの医学知識ファイルを読み込み中...")
    file = load_random_md_file()

    if not file:
        print("ファイルが見つかりませんでした。パスを確認してください。")
        return

    print(f"選択ファイル: {file['filename']}")
    print("\nClaude が記事を生成中...")

    article = generate_article(file)

    print(f"\n【生成タイトル】{article['title']}")
    print("\n" + "=" * 50)
    print(article["content"])
    print("=" * 50)

    filepath = save_article(article["title"], article["content"])

    print("\ngit push を実行中...")
    try:
        git_push(filepath)
        print("\n🎉 Zennへの記事アップロード完了！")
    except RuntimeError as e:
        print(f"\n❌ git push に失敗しました。手動でpushしてください。")
        print(f"   ファイル: {filepath}")
        print(f"   エラー: {e}")


if __name__ == "__main__":
    main()
