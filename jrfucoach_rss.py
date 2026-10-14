import hashlib
import os
import re
import time
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime, parsedate_to_datetime
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
import xml.etree.ElementTree as ET


SOURCE_URL = "https://www.jrfucoach.com/ブログ/"
OUTPUT_FILE = "jrfucoach.xml"

JST = timezone(timedelta(hours=9))

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/154.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,"
        "application/xml;q=0.9,image/avif,"
        "image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
}


def clean_text(text):
    return re.sub(r"\s+", " ", text).strip()


def canonicalize_url(url):
    parsed = urlparse(url)

    path = parsed.path

    if path != "/" and path.endswith("/"):
        path = path.rstrip("/") + "/"

    return (
        f"{parsed.scheme}://"
        f"{parsed.netloc}"
        f"{path}"
    )


def make_guid(link):
    return hashlib.sha256(
        link.encode("utf-8")
    ).hexdigest()


def fetch_page(url):
    for attempt in range(1, 4):
        try:
            print(f"取得 {attempt}/3: {url}")

            response = requests.get(
                url,
                headers=HEADERS,
                timeout=30,
            )

            print(f"HTTP: {response.status_code}")

            response.raise_for_status()

            response.encoding = (
                response.apparent_encoding
                or "utf-8"
            )

            return response.text

        except requests.RequestException as e:
            print(f"取得失敗: {e}")

            if attempt < 3:
                print("10秒待って再試行します。")
                time.sleep(10)

    return None


def extract_date_from_url(url):
    path = urlparse(url).path

    match = re.search(
        r"/(20\d{2})/"
        r"(\d{1,2})/"
        r"(\d{1,2})/",
        path,
    )

    if not match:
        return None

    try:
        return datetime(
            int(match.group(1)),
            int(match.group(2)),
            int(match.group(3)),
            12,
            0,
            0,
            tzinfo=JST,
        )

    except ValueError:
        return None


def is_article_url(url):
    parsed = urlparse(url)

    if parsed.netloc not in (
        "www.jrfucoach.com",
        "jrfucoach.com",
    ):
        return False

    return bool(
        re.search(
            r"/20\d{2}/"
            r"\d{1,2}/"
            r"\d{1,2}/"
            r".+",
            parsed.path,
        )
    )


def load_existing_items():
    items = {}

    if not os.path.exists(OUTPUT_FILE):
        return items

    try:
        tree = ET.parse(OUTPUT_FILE)
        root = tree.getroot()

        for item in root.findall(
            "./channel/item"
        ):
            link = (
                item.findtext(
                    "link",
                    "",
                )
                .strip()
            )

            guid = (
                item.findtext(
                    "guid",
                    "",
                )
                .strip()
            )

            if not guid and link:
                guid = make_guid(link)

            if not guid:
                continue

            items[guid] = {
                "title": item.findtext(
                    "title",
                    "",
                ),
                "link": link,
                "description": item.findtext(
                    "description",
                    "",
                ),
                "pubDate": item.findtext(
                    "pubDate",
                    "",
                ),
                "guid": guid,
                "category": item.findtext(
                    "category",
                    "",
                ),
            }

    except Exception as e:
        print(
            f"既存RSS読込失敗: {e}"
        )

    return items


def find_category(a):
    current = a

    for _ in range(8):
        current = current.parent

        if current is None:
            break

        text = clean_text(
            current.get_text(
                " ",
                strip=True,
            )
        )

        if "資格認定講習" in text:
            return "資格認定講習"

        if "トピックス" in text:
            return "トピックス"

        if len(text) > 2000:
            break

    return ""


def extract_articles(html):
    soup = BeautifulSoup(
        html,
        "html.parser",
    )

    articles = {}

    for a in soup.find_all(
        "a",
        href=True,
    ):
        link = canonicalize_url(
            urljoin(
                SOURCE_URL,
                a["href"],
            )
        )

        if not is_article_url(link):
            continue

        title = clean_text(
            a.get_text(
                " ",
                strip=True,
            )
        )

        if not title:
            continue

        if title in (
            "続きを読む",
            "more",
            ">>>more",
        ):
            # 「続きを読む」リンクの場合、
            # 同じ記事ブロック内の見出しを探す
            current = a.parent

            found_title = ""

            for _ in range(6):
                if current is None:
                    break

                candidates = (
                    current.find_all(
                        ["h1", "h2", "h3", "h4", "h5", "a"]
                    )
                )

                for candidate in candidates:
                    candidate_text = clean_text(
                        candidate.get_text(
                            " ",
                            strip=True,
                        )
                    )

                    candidate_href = (
                        candidate.get("href")
                        if candidate.name == "a"
                        else None
                    )

                    if (
                        candidate_text
                        and candidate_text
                        not in (
                            "続きを読む",
                            "more",
                            ">>>more",
                        )
                    ):
                        if candidate_href:
                            candidate_url = (
                                canonicalize_url(
                                    urljoin(
                                        SOURCE_URL,
                                        candidate_href,
                                    )
                                )
                            )

                            if (
                                candidate_url
                                != link
                            ):
                                continue

                        found_title = (
                            candidate_text
                        )
                        break

                if found_title:
                    break

                current = current.parent

            title = found_title

        if not title:
            continue

        if len(title) < 5:
            continue

        published = extract_date_from_url(
            link
        )

        if published is None:
            continue

        category = find_category(a)

        # 同じ記事URLがタイトルリンクと
        # 「続きを読む」の両方で現れるため重複排除
        existing = articles.get(link)

        candidate = {
            "title": title,
            "link": link,
            "date": published,
            "category": category,
        }

        if existing is None:
            articles[link] = candidate

        else:
            # 「続きを読む」などより
            # 情報量のあるタイトルを残す
            if (
                len(title)
                > len(existing["title"])
            ):
                articles[link] = candidate

    result = list(
        articles.values()
    )

    result.sort(
        key=lambda x: (
            x["date"],
            x["link"],
        ),
        reverse=True,
    )

    return result


def build_rss(
    existing_items,
    current_articles,
):
    merged = dict(existing_items)

    new_count = 0

    for article in current_articles:
        guid = make_guid(
            article["link"]
        )

        if guid not in merged:
            new_count += 1

        description_parts = []

        if article["category"]:
            description_parts.append(
                f"カテゴリ: "
                f"{article['category']}"
            )

        description_parts.append(
            "JRFUコーチネット"
        )

        merged[guid] = {
            "title": article["title"],
            "link": article["link"],
            "description": " / ".join(
                description_parts
            ),
            "pubDate": format_datetime(
                article["date"]
            ),
            "guid": guid,
            "category": article[
                "category"
            ],
        }

    def get_pubdate(item):
        try:
            dt = parsedate_to_datetime(
                item["pubDate"]
            )

            if dt.tzinfo is None:
                dt = dt.replace(
                    tzinfo=timezone.utc
                )

            return dt

        except Exception:
            return datetime(
                1970,
                1,
                1,
                tzinfo=timezone.utc,
            )

    sorted_items = sorted(
        merged.values(),
        key=get_pubdate,
        reverse=True,
    )

    rss = ET.Element(
        "rss",
        version="2.0",
    )

    channel = ET.SubElement(
        rss,
        "channel",
    )

    ET.SubElement(
        channel,
        "title",
    ).text = "JRFUコーチネット"

    ET.SubElement(
        channel,
        "link",
    ).text = SOURCE_URL

    ET.SubElement(
        channel,
        "description",
    ).text = (
        "JRFUコーチネット ブログの新着情報"
    )

    ET.SubElement(
        channel,
        "language",
    ).text = "ja"

    ET.SubElement(
        channel,
        "lastBuildDate",
    ).text = format_datetime(
        datetime.now(JST)
    )

    for data in sorted_items:
        item = ET.SubElement(
            channel,
            "item",
        )

        ET.SubElement(
            item,
            "title",
        ).text = data["title"]

        ET.SubElement(
            item,
            "link",
        ).text = data["link"]

        ET.SubElement(
            item,
            "description",
        ).text = data[
            "description"
        ]

        ET.SubElement(
            item,
            "pubDate",
        ).text = data[
            "pubDate"
        ]

        if data.get("category"):
            ET.SubElement(
                item,
                "category",
            ).text = data[
                "category"
            ]

        guid_element = ET.SubElement(
            item,
            "guid",
        )

        guid_element.set(
            "isPermaLink",
            "false",
        )

        guid_element.text = data[
            "guid"
        ]

    tree = ET.ElementTree(rss)

    ET.indent(
        tree,
        space="  ",
    )

    tree.write(
        OUTPUT_FILE,
        encoding="utf-8",
        xml_declaration=True,
    )

    return (
        len(sorted_items),
        new_count,
    )


def main():
    existing_items = (
        load_existing_items()
    )

    print(
        f"既存RSS件数: "
        f"{len(existing_items)}"
    )

    html = fetch_page(
        SOURCE_URL
    )

    if html is None:
        if existing_items:
            print(
                "取得に失敗したため、"
                "既存XMLを維持します。"
            )
            return

        raise RuntimeError(
            "JRFUコーチネットの"
            "取得に失敗しました。"
        )

    print(
        f"HTML文字数: {len(html)}"
    )

    articles = extract_articles(
        html
    )

    print()
    print(
        f"記事取得件数: "
        f"{len(articles)}"
    )

    print()
    print(
        "=== 取得記事 ==="
    )

    for i, article in enumerate(
        articles,
        1,
    ):
        category = (
            f"[{article['category']}] "
            if article["category"]
            else ""
        )

        print()
        print(
            f"{i:2d}. "
            f"{article['date'].strftime('%Y-%m-%d')} "
            f"{category}"
            f"{article['title']}"
        )

        print(
            f"    {article['link']}"
        )

    if len(articles) < 3:
        if existing_items:
            print()
            print(
                "記事取得件数が異常に少ないため、"
                "既存XMLを維持します。"
            )
            return

        raise RuntimeError(
            f"記事取得件数が少なすぎます: "
            f"{len(articles)}"
        )

    total_count, new_count = (
        build_rss(
            existing_items,
            articles,
        )
    )

    print()
    print(
        f"今回の新規記事: "
        f"{new_count}"
    )

    print(
        f"RSS保存件数: "
        f"{total_count}"
    )

    print(
        f"保存先: "
        f"{os.path.abspath(OUTPUT_FILE)}"
    )


if __name__ == "__main__":
    main()