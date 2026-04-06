import io
import zipfile
import xml.etree.ElementTree as ET

import httpx
from langchain_core.tools import tool

from app.core.config import settings

# 기업코드 캐시 (서버 기동 중 메모리에 유지, 한국 주식 한글명 → 종목코드 변환용)
_corp_code_cache: list[dict] | None = None


async def _load_corp_codes() -> list[dict]:
    """DART corpCode.xml ZIP을 다운로드하여 기업코드 목록을 반환합니다 (캐시 사용)."""
    global _corp_code_cache
    if _corp_code_cache is not None:
        return _corp_code_cache

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.get(
                "https://opendart.fss.or.kr/api/corpCode.xml",
                params={"crtfc_key": settings.DART_API_KEY},
            )
            resp.raise_for_status()

        z = zipfile.ZipFile(io.BytesIO(resp.content))
        xml_name = z.namelist()[0]
        tree = ET.parse(z.open(xml_name))
        root = tree.getroot()

        corps = []
        for item in root.iter("list"):
            corp_code = item.findtext("corp_code", "")
            corp_name = item.findtext("corp_name", "")
            stock_code = item.findtext("stock_code", "")
            if corp_code and corp_name:
                corps.append({
                    "corp_code": corp_code,
                    "corp_name": corp_name,
                    "stock_code": stock_code.strip(),
                })

        _corp_code_cache = corps
        return corps
    except Exception:
        return []


@tool
async def naver_search(query: str) -> str:
    """네이버에서 최신 뉴스를 검색합니다. IPO, 공모주 관련 최신 소식을 찾을 때 유용합니다.

    Args:
        query: 검색할 키워드 (예: "삼성전자 IPO", "공모주 청약 일정")
    """
    if not settings.NAVER_CLIENT_ID or not settings.NAVER_CLIENT_SECRET:
        return "네이버 검색을 사용하려면 .env에 NAVER_CLIENT_ID와 NAVER_CLIENT_SECRET을 설정해주세요."

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                "https://openapi.naver.com/v1/search/news.json",
                params={"query": query, "display": 5, "sort": "date"},
                headers={
                    "X-Naver-Client-Id": settings.NAVER_CLIENT_ID,
                    "X-Naver-Client-Secret": settings.NAVER_CLIENT_SECRET,
                },
            )
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPStatusError as e:
        return f"네이버 검색 실패: HTTP {e.response.status_code} 오류"
    except httpx.RequestError:
        return "네이버 검색 실패: 네트워크 연결 오류"

    items = data.get("items", [])
    if not items:
        return f"'{query}'에 대한 검색 결과가 없습니다."

    import re
    results = []
    for item in items:
        # HTML 태그 제거
        title = re.sub(r"<[^>]+>", "", item.get("title", ""))
        desc = re.sub(r"<[^>]+>", "", item.get("description", ""))
        pub_date = item.get("pubDate", "")
        link = item.get("link", "")
        results.append(f"- [{title}]({link})\n  {desc}\n  ({pub_date})")

    return f"'{query}' 네이버 뉴스 검색 결과 ({len(items)}건):\n\n" + "\n\n".join(results)


@tool
async def get_stock_price(query: str) -> str:
    """한국 및 해외 주식의 현재가, 등락률, 거래량 등 실시간 시세를 조회합니다.

    Args:
        query: 회사명 또는 티커 심볼 (예: "삼성전자", "카카오", "AAPL", "테슬라", "NVDA", "7203.T")
    """
    import yfinance as yf

    # 해외 주식 한글명 → 티커 매핑
    _global_name_to_ticker = {
        "애플": "AAPL", "테슬라": "TSLA", "엔비디아": "NVDA",
        "마이크로소프트": "MSFT", "구글": "GOOGL", "알파벳": "GOOGL",
        "아마존": "AMZN", "메타": "META", "페이스북": "META",
        "넷플릭스": "NFLX", "디즈니": "DIS", "나이키": "NKE",
        "코카콜라": "KO", "맥도날드": "MCD", "스타벅스": "SBUX",
        "버크셔해서웨이": "BRK-B", "JP모건": "JPM", "골드만삭스": "GS",
        "인텔": "INTC", "AMD": "AMD", "퀄컴": "QCOM",
        "비자": "V", "마스터카드": "MA", "페이팔": "PYPL",
        "화이자": "PFE", "존슨앤존슨": "JNJ", "모더나": "MRNA",
        "보잉": "BA", "에어버스": "EADSY",
        "토요타": "7203.T", "소니": "6758.T", "닌텐도": "7974.T",
        "텐센트": "0700.HK", "알리바바": "BABA", "바이두": "BIDU",
        "TSMC": "TSM", "삼성SDI": "006400.KS",
    }

    # 1) 해외 주식 한글명 매핑 확인
    if query in _global_name_to_ticker:
        ticker_symbol = _global_name_to_ticker[query]
        try:
            ticker = yf.Ticker(ticker_symbol)
            info = ticker.info
            if info and "currentPrice" in info:
                currency = info.get("currency", "USD")
                currency_symbol = {"USD": "$", "JPY": "¥", "HKD": "HK$", "EUR": "€", "GBP": "£"}.get(currency, currency)
                return _format_stock_info(info, info.get("shortName", query), ticker_symbol, currency_symbol)
        except Exception as e:
            return f"주가 조회 중 오류 발생: {type(e).__name__}"

    # 2) 한국 주식 — DART 기업코드에서 종목코드 검색
    corps = await _load_corp_codes()
    stock_code = None
    corp_name_found = None
    for c in corps:
        if c["stock_code"] and (c["corp_name"] == query or query in c["corp_name"]):
            stock_code = c["stock_code"]
            corp_name_found = c["corp_name"]
            break

    if stock_code:
        try:
            for suffix in (".KS", ".KQ"):
                ticker_symbol = f"{stock_code}{suffix}"
                ticker = yf.Ticker(ticker_symbol)
                info = ticker.info
                if info and "currentPrice" in info:
                    return _format_stock_info(info, corp_name_found, stock_code, "원")
            return f"'{query}'({stock_code})의 시세 정보를 가져올 수 없습니다."
        except Exception as e:
            return f"주가 조회 중 오류 발생: {type(e).__name__}"

    # 3) 티커 심볼로 직접 조회 (예: "AAPL", "005930.KS", "7203.T")
    ticker_symbol = query.upper()
    try:
        ticker = yf.Ticker(ticker_symbol)
        info = ticker.info
        if not info or "currentPrice" not in info:
            return f"'{query}'의 시세 정보를 가져올 수 없습니다. 정확한 회사명 또는 티커 심볼을 확인해주세요."
        currency = info.get("currency", "USD")
        currency_symbol = {"USD": "$", "JPY": "¥", "HKD": "HK$", "EUR": "€", "GBP": "£"}.get(currency, currency)
        return _format_stock_info(info, info.get("shortName", query), ticker_symbol, currency_symbol)
    except Exception as e:
        return f"주가 조회 중 오류 발생: {type(e).__name__}"


def _format_stock_info(info: dict, name: str, code: str, currency: str) -> str:
    """주식 시세 정보를 포맷팅합니다."""
    current_price = info.get("currentPrice", 0)
    previous_close = info.get("previousClose", 0)
    change = current_price - previous_close if previous_close else 0
    change_pct = (change / previous_close * 100) if previous_close else 0
    sign = "+" if change >= 0 else ""

    # 통화에 따라 소수점 처리
    is_won = currency == "원"
    fmt = ",.0f" if is_won else ",.2f"

    lines = [
        f"종목: {name} ({code})",
        f"현재가: {current_price:{fmt}}{currency}",
        f"전일대비: {sign}{change:{fmt}}{currency} ({sign}{change_pct:.2f}%)",
        f"전일종가: {previous_close:{fmt}}{currency}",
    ]

    for label, key in [("시가", "open"), ("고가", "dayHigh"), ("저가", "dayLow")]:
        val = info.get(key)
        if isinstance(val, (int, float)):
            lines.append(f"{label}: {val:{fmt}}{currency}")

    vol = info.get("volume")
    if isinstance(vol, (int, float)):
        lines.append(f"거래량: {vol:,}주")

    market_cap = info.get("marketCap")
    if market_cap:
        if is_won:
            lines.append(f"시가총액: {market_cap / 1_0000_0000:,.0f}억원")
        else:
            lines.append(f"시가총액: {currency}{market_cap / 1_000_000_000:,.2f}B")

    for label, key in [("52주 최고", "fiftyTwoWeekHigh"), ("52주 최저", "fiftyTwoWeekLow")]:
        val = info.get(key)
        if isinstance(val, (int, float)):
            lines.append(f"{label}: {val:{fmt}}{currency}")

    per = info.get("trailingPE")
    if isinstance(per, (int, float)):
        lines.append(f"PER: {per:.2f}")

    eps = info.get("trailingEps")
    if isinstance(eps, (int, float)):
        lines.append(f"EPS: {eps:{fmt}}{currency}")

    return "\n".join(lines)
