import argparse
import json
import logging
import os
import re
import time
from notion_client import Client
import requests

from datetime import datetime, timedelta
import hashlib
from notion_helper import NotionHelper
from weread_api import WeReadApi

from utils import (
    format_date,
    format_time,
    get_callout,
    get_date,
    get_file,
    get_heading,
    get_icon,
    get_number,
    get_number_from_result,
    get_quote,
    get_relation,
    get_rich_text,
    get_rich_text_from_result,
    get_table_of_contents,
    get_title,
    get_url,
)

TAG_ICON_URL = "https://www.notion.so/icons/tag_gray.svg"
USER_ICON_URL = "https://www.notion.so/icons/user-circle-filled_gray.svg"
TARGET_ICON_URL = "https://www.notion.so/icons/target_red.svg"
BOOKMARK_ICON_URL = "https://www.notion.so/icons/bookmark_gray.svg"


def get_bookmark_list(page_id, bookId):
    """获取我的划线"""
    filter = {"property": "书籍", "relation": {"contains": page_id}}
    results = notion_helper.query_all_by_book(
        notion_helper.bookmark_database_id, filter
    )
    dict1 = {
        get_rich_text_from_result(x, "bookmarkId"): get_rich_text_from_result(
            x, "blockId"
        )
        for x in results
    }
    dict2 = {get_rich_text_from_result(x, "blockId"): x.get("id") for x in results}
    bookmarks = weread_api.get_bookmark_list(bookId)
    for i in bookmarks:
        if i.get("bookmarkId") in dict1:
            i["blockId"] = dict1.pop(i.get("bookmarkId"))
    for blockId in dict1.values():
        notion_helper.delete_block(blockId)
        notion_helper.delete_block(dict2.get(blockId))
    return bookmarks


def get_review_list(page_id, bookId):
    """获取笔记"""
    filter = {"property": "书籍", "relation": {"contains": page_id}}
    results = notion_helper.query_all_by_book(notion_helper.review_database_id, filter)
    dict1 = {
        get_rich_text_from_result(x, "reviewId"): get_rich_text_from_result(
            x, "blockId"
        )
        for x in results
    }
    dict2 = {get_rich_text_from_result(x, "blockId"): x.get("id") for x in results}
    reviews = weread_api.get_review_list(bookId)
    for i in reviews:
        if i.get("reviewId") in dict1:
            i["blockId"] = dict1.pop(i.get("reviewId"))
    for blockId in dict1.values():
        notion_helper.delete_block(blockId)
        notion_helper.delete_block(dict2.get(blockId))
    return reviews


def check(bookId):
    """检查是否已经插入过"""
    filter = {"property": "BookId", "rich_text": {"equals": bookId}}
    response = notion_helper.query(
        database_id=notion_helper.book_database_id, filter=filter
    )
    if len(response["results"]) > 0:
        return response["results"][0]["id"]
    return None


def insert_book_to_notion(
        page_id, bookName, bookId, cover, author, isbn, rating, categories, sort
):
    """插入到notion"""
    parent = {"database_id": notion_helper.book_database_id, "type": "database_id"}
    properties = {
        "书名": get_title(bookName),
        "BookId": get_rich_text(bookId),
        "ISBN": get_rich_text(isbn),
        "链接": get_url(weread_api.get_url(bookId)),
        "作者": get_relation(
            [
                notion_helper.get_relation_id(
                    x, notion_helper.author_database_id, USER_ICON_URL
                )
                for x in author.split(" ")
            ]
        ),
        "Sort": get_number(sort),
        "评分": get_number(rating),
        "封面": get_file(cover),
    }
    if categories != None:
        properties["分类"] = get_relation(
            [
                notion_helper.get_relation_id(
                    x, notion_helper.category_database_id, TAG_ICON_URL
                )
                for x in categories
            ]
        )
    read_info = weread_api.get_read_info(bookId=bookId)
    if read_info != None:
        markedStatus = read_info.get("markedStatus", 0)
        readingTime = format_time(read_info.get("readingTime", 0))
        readingProgress = (
            100 if (markedStatus == 4) else read_info.get("readingProgress", 0)
        )
        totalReadDay = read_info.get("totalReadDay", 0)
        properties["阅读状态"] = {"status": {"name": "已读" if markedStatus == 4 else "在读"}}
        properties["阅读时长"] = get_rich_text(readingTime)
        properties["阅读进度"] = {"number": readingProgress / 100}
        properties["阅读天数"] = {"number": totalReadDay}
        finishedDate = int(datetime.timestamp(datetime.now()))
        if "finishedDate" in read_info:
            finishedDate = read_info.get("finishedDate")
        elif "readDetail" in read_info:
            if "lastReadingDate" in read_info.get("readDetail"):
                finishedDate = read_info.get("readDetail").get("lastReadingDate")
                lastReadingDate = datetime.utcfromtimestamp(
                    read_info.get("readDetail").get("lastReadingDate")
                ) + timedelta(hours=8)
                properties["最后阅读时间"] = get_date(
                    lastReadingDate.strftime("%Y-%m-%d %H:%M:%S")
                )
        elif "readingBookDate" in read_info:
            finishedDate = read_info.get("readingBookDate")
        finishedDate = datetime.utcfromtimestamp(finishedDate) + timedelta(hours=8)
        properties["时间"] = get_date(finishedDate.strftime("%Y-%m-%d %H:%M:%S"))
        if "readDetail" in read_info and "beginReadingDate" in read_info.get(
                "readDetail"
        ):
            lastReadingDate = datetime.utcfromtimestamp(
                read_info.get("readDetail").get("beginReadingDate")
            ) + timedelta(hours=8)
            properties["开始阅读时间"] = get_date(
                lastReadingDate.strftime("%Y-%m-%d %H:%M:%S")
            )

        if (
                read_info.get("bookInfo") != None
                and read_info.get("bookInfo").get("intro") != None
        ):
            properties["简介"] = get_rich_text(read_info.get("bookInfo").get("intro"))
        notion_helper.get_date_relation(properties, finishedDate)
    if cover.startswith("http"):
        icon = get_icon(cover)
    else:
        icon = get_icon(BOOKMARK_ICON_URL)
    # notion api 限制100个block
    if page_id == None:
        response = notion_helper.create_page(
            parent=parent, icon=icon, properties=properties
        )
        page_id = response["id"]
    else:
        notion_helper.update_page(page_id=page_id, icon=icon, properties=properties)
    return page_id


def get_sort():
    """获取database中的最新时间"""
    filter = {"property": "Sort", "number": {"is_not_empty": True}}
    sorts = [
        {
            "property": "Sort",
            "direction": "descending",
        }
    ]
    response = notion_helper.query(
        database_id=notion_helper.book_database_id,
        filter=filter,
        sorts=sorts,
        page_size=1,
    )
    if len(response.get("results")) == 1:
        return response.get("results")[0].get("properties").get("Sort").get("number")
    return 0


def download_image(url, save_dir="cover"):
    # 确保目录存在，如果不存在则创建
    if not os.path.exists(save_dir):
        os.makedirs(save_dir)

    # 获取文件名，使用 URL 最后一个 '/' 之后的字符串
    file_name = url.split("/")[-1] + ".jpg"
    save_path = os.path.join(save_dir, file_name)

    # 检查文件是否已经存在，如果存在则不进行下载
    if os.path.exists(save_path):
        print(f"File {file_name} already exists. Skipping download.")
        return save_path

    response = requests.get(url, stream=True)
    if response.status_code == 200:
        with open(save_path, "wb") as file:
            for chunk in response.iter_content(chunk_size=128):
                file.write(chunk)
        print(f"Image downloaded successfully to {save_path}")
    else:
        print(f"Failed to download image. Status code: {response.status_code}")
    return save_path


def sort_notes(page_id, chapter=None, bookmark_list=None):
    """对笔记进行排序"""
    notes = []
    if chapter != None:
        filter = {"property": "书籍", "relation": {"contains": page_id}}
        results = notion_helper.query_all_by_book(
            notion_helper.chapter_database_id, filter
        )
        dict1 = {
            get_number_from_result(x, "chapterUid"): get_rich_text_from_result(
                x, "blockId"
            )
            for x in results
        }
        dict2 = {get_rich_text_from_result(x, "blockId"): x.get("id") for x in results}
        if bookmark_list != None:
            bookmark_list = sorted(
                bookmark_list,
                key=lambda x: (
                    x.get("chapterUid", 1),
                    0
                    if (x.get("range", "") == "" or x.get("range").split("-")[0] == "")
                    else int(x.get("range").split("-")[0]),
                ),
            )
            d = {}
            for data in bookmark_list:
                chapterUid = data.get("chapterUid", 1)
                if chapterUid not in d:
                    d[chapterUid] = []
                d[chapterUid].append(data)
            for key, value in d.items():
                if key in chapter:
                    if key in dict1:
                        chapter.get(key)["blockId"] = dict1.pop(key)
                    notes.append(chapter.get(key))
                notes.extend(value)
            for blockId in dict1.values():
                notion_helper.delete_block(blockId)
                notion_helper.delete_block(dict2.get(blockId))
    elif bookmark_list!=None: notes.extend(bookmark_list)
    return notes


def append_blocks(id, contents):
    print(f"笔记数{len(contents)}")
    before_block_id = ""
    block_children = notion_helper.get_block_children(id)
    if len(block_children) > 0 and block_children[0].get("type") == "table_of_contents":
        before_block_id = block_children[0].get("id")
    else:
        response = notion_helper.append_blocks(
            block_id=id, children=[get_table_of_contents()]
        )
        before_block_id = response.get("results")[0].get("id")
    blocks = []
    sub_contents = []
    l = []
    for content in contents:
        if len(blocks) == 100:
            results = append_blocks_to_notion(id, blocks, before_block_id, sub_contents)
            before_block_id = results[-1].get("blockId")
            l.extend(results)
            blocks.clear()
            sub_contents.clear()
            blocks.append(content_to_block(content))
            sub_contents.append(content)
        elif "blockId" in content:
            if len(blocks) > 0:
                l.extend(
                    append_blocks_to_notion(id, blocks, before_block_id, sub_contents)
                )
                blocks.clear()
                sub_contents.clear()
            before_block_id = content["blockId"]
        else:
            blocks.append(content_to_block(content))
            sub_contents.append(content)

    if len(blocks) > 0:
        l.extend(append_blocks_to_notion(id, blocks, before_block_id, sub_contents))
    for index, value in enumerate(l):
        print(f"正在插入第{index + 1}条笔记，共{len(l)}条")
        if "bookmarkId" in value:
            notion_helper.insert_bookmark(id, value)
        elif "reviewId" in value:
            notion_helper.insert_review(id, value)
        else:
            notion_helper.insert_chapter(id, value)


def content_to_block(content):
    if "bookmarkId" in content:
        return get_callout(
            content.get("markText"),
            content.get("style"),
            content.get("colorStyle"),
            content.get("reviewId"),
        )
    elif "reviewId" in content:
        return get_callout(
            content.get("content"),
            content.get("style"),
            content.get("colorStyle"),
            content.get("reviewId"),
        )
    else:
        return get_heading(content.get("level"), content.get("title"))


def append_blocks_to_notion(id, blocks, after, contents):
    response = notion_helper.append_blocks_after(
        block_id=id, children=blocks, after=after
    )
    results = response.get("results")
    l = []
    for index, content in enumerate(contents):
        result = results[index]
        if content.get("abstract") != None and content.get("abstract") != "":
            notion_helper.append_blocks(
                block_id=result.get("id"), children=[get_quote(content.get("abstract"))]
            )
        content["blockId"] = result.get("id")
        l.append(content)
    return l

def consolidate2Page(bookId,book,sort,repository,branch):
    title = book.get("title")
    cover = book.get("cover")
    author = book.get("author")
    if author == "公众号" and book.get("cover").endswith("/0"):
        cover += ".jpg"
    if cover.startswith("http") and not cover.endswith(".jpg"):
        path = download_image(cover)
        cover = (
            f"https://raw.githubusercontent.com/{repository}/{branch}/{path}"
        )
    categories = book.get("categories")
    isbn, rating = weread_api.get_bookinfo(bookId)
    if categories != None:
        categories = [x["title"] for x in categories]
    #开始填写笔记
    page_id = check(bookId)
    page_id = insert_book_to_notion(
        page_id, title, bookId, cover, author, isbn, rating, categories, sort
    )
    return page_id

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    options = parser.parse_args()
    weread_cookie = os.getenv("WEREAD_COOKIE")
    branch = os.getenv("REF").split("/")[-1]
    repository = os.getenv("REPOSITORY")
    weread_api = WeReadApi()
    notion_helper = NotionHelper()
    latest_sort = get_sort()  # 从 Notion 中获取,有笔记的才有 sort
    shelfBooks = weread_api.get_bookshelf()
    notedBooks = weread_api.get_notebooklist()
    booksWithNotesIDList = [x['bookId'] for x in notedBooks]
    if shelfBooks != None:
        for book in shelfBooks:
            bookId = book.get("bookId")
            if bookId in booksWithNotesIDList:
                continue
            sort = -1
            consolidate2Page(bookId,book,sort,repository,branch)

    if notedBooks != None:
        for index, book in enumerate(notedBooks):
            sort = book.get("sort")
            if sort <= latest_sort: # 用 sort 确认是否同步过
                continue
            book = book.get("book")
            bookId = book.get("bookId")
            page_id = consolidate2Page(bookId,book,sort,repository,branch)
            chapter = weread_api.get_chapter_info(bookId)
            bookmark_list = get_bookmark_list(page_id, bookId)
            reviews = get_review_list(page_id, bookId)
            bookmark_list.extend(reviews)
            content = sort_notes(page_id, chapter, bookmark_list)
            append_blocks(page_id, content)
