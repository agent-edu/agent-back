import io
import zipfile
import xml.etree.ElementTree as ET

import httpx
from langchain_core.tools import tool

from app.core.config import settings

DART_BASE_URL = "https://opendart.fss.or.kr/api"

# 기업코드 캐시 (서버 기동 중 메모리에 유지)
_corp_code_cache: list[dict] | None = None


async def _dart_get(path: str, params: dict) -> dict:
    """DART API GET 요청을 수행하고 JSON 응답을 반환합니다."""
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(f"{DART_BASE_URL}/{path}", params=params)
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPStatusError as e:
        return {"status": "HTTP_ERROR", "message": f"HTTP {e.response.status_code} 오류"}
    except httpx.RequestError:
        return {"status": "NETWORK_ERROR", "message": "네트워크 연결 오류"}
    except Exception:
        return {"status": "UNKNOWN_ERROR", "message": "응답 처리 중 오류 발생"}


async def _load_corp_codes() -> list[dict]:
    """DART corpCode.xml ZIP을 다운로드하여 기업코드 목록을 반환합니다 (캐시 사용)."""
    global _corp_code_cache
    if _corp_code_cache is not None:
        return _corp_code_cache

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.get(
                f"{DART_BASE_URL}/corpCode.xml",
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


async def _find_corp_code(company_name: str) -> str | None:
    """회사명으로 DART 기업코드를 검색합니다."""
    corps = await _load_corp_codes()
    if not corps:
        return None

    # 1) 정확히 일치하는 상장사 우선
    for c in corps:
        if c["corp_name"] == company_name and c["stock_code"]:
            return c["corp_code"]

    # 2) 정확히 일치 (비상장 포함)
    for c in corps:
        if c["corp_name"] == company_name:
            return c["corp_code"]

    # 3) 회사명을 포함하는 상장사
    for c in corps:
        if company_name in c["corp_name"] and c["stock_code"]:
            return c["corp_code"]

    # 4) 회사명을 포함 (비상장 포함)
    for c in corps:
        if company_name in c["corp_name"]:
            return c["corp_code"]

    return None


@tool
async def search_ipo_disclosure(
    corp_name: str = "",
    begin_date: str = "",
    end_date: str = "",
) -> str:
    """DART에서 IPO 관련 공시(증권신고서, 투자설명서 등)를 검색합니다.

    Args:
        corp_name: 회사명 (빈 문자열이면 전체 검색)
        begin_date: 검색 시작일 (YYYYMMDD 형식, 예: 20240101)
        end_date: 검색 종료일 (YYYYMMDD 형식, 예: 20240331)
    """
    params = {
        "crtfc_key": settings.DART_API_KEY,
        "pblntf_ty": "I",  # I: 증권신고(지분증권)
        "page_count": 10,
    }
    if corp_name:
        corp_code = await _find_corp_code(corp_name)
        if corp_code:
            params["corp_code"] = corp_code
    if begin_date:
        params["bgn_de"] = begin_date
    if end_date:
        params["end_de"] = end_date

    data = await _dart_get("list.json", params)

    if data.get("status") != "000":
        return f"공시 검색 실패: {data.get('message', '알 수 없는 오류')}"

    items = data.get("list", [])
    if not items:
        return "검색 결과가 없습니다."

    results = []
    for item in items:
        results.append(
            f"- [{item.get('report_nm', '')}] {item.get('corp_name', '')} "
            f"(접수일: {item.get('rcept_dt', '')}, 접수번호: {item.get('rcept_no', '')})"
        )
    return f"총 {len(items)}건의 IPO 관련 공시:\n" + "\n".join(results)


@tool
async def get_company_info(company_name: str) -> str:
    """DART에서 기업 개황(업종, 대표자, 설립일, 홈페이지 등)을 조회합니다.

    Args:
        company_name: 조회할 회사명
    """
    corp_code = await _find_corp_code(company_name)
    if not corp_code:
        return f"'{company_name}'에 해당하는 기업을 찾을 수 없습니다."

    params = {
        "crtfc_key": settings.DART_API_KEY,
        "corp_code": corp_code,
    }
    data = await _dart_get("company.json", params)

    if data.get("status") != "000":
        return f"기업 정보 조회 실패: {data.get('message', '알 수 없는 오류')}"

    info_lines = [
        f"회사명: {data.get('corp_name', '')}",
        f"영문명: {data.get('corp_name_eng', '')}",
        f"종목코드: {data.get('stock_code', '')}",
        f"대표자: {data.get('ceo_nm', '')}",
        f"법인구분: {data.get('corp_cls', '')}",
        f"업종코드: {data.get('induty_code', '')}",
        f"설립일: {data.get('est_dt', '')}",
        f"결산월: {data.get('acc_mt', '')}",
        f"홈페이지: {data.get('hm_url', '')}",
        f"전화번호: {data.get('phn_no', '')}",
        f"주소: {data.get('adres', '')}",
    ]
    return "\n".join(info_lines)


@tool
async def get_ipo_price_info(corp_name: str) -> str:
    """DART 증권신고서에서 공모가격 정보(공모가 밴드, 확정 공모가, 공모 주식수 등)를 조회합니다.

    Args:
        corp_name: 조회할 회사명
    """
    corp_code = await _find_corp_code(corp_name)
    if not corp_code:
        return f"'{corp_name}'에 해당하는 기업을 찾을 수 없습니다."

    # 증권신고서 - 증권 발행실적 조회 (최근 사업연도)
    from datetime import datetime
    current_year = str(datetime.now().year)

    for year in [current_year, str(int(current_year) - 1)]:
        for reprt_code in ["11011", "11012", "11013", "11014"]:
            params = {
                "crtfc_key": settings.DART_API_KEY,
                "corp_code": corp_code,
                "bsns_year": year,
                "reprt_code": reprt_code,
            }
            data = await _dart_get("irdsSttus.json", params)

            if data.get("status") == "000" and data.get("list"):
                items = data["list"]
                results = []
                for item in items:
                    results.append(
                        f"- 증권종류: {item.get('stk_knd', '')}, "
                        f"발행주식수: {item.get('stk_cnt', '')}, "
                        f"액면가: {item.get('fv', '')}, "
                        f"발행가: {item.get('issue_p', '')}"
                    )
                return f"{corp_name} 공모가격 정보 ({year}년):\n" + "\n".join(results)

    return await _fallback_ipo_info(corp_name, corp_code)


async def _fallback_ipo_info(corp_name: str, corp_code: str) -> str:
    """증권신고서 API 실패 시 공시 목록에서 IPO 관련 공시를 검색하여 요약합니다."""
    params = {
        "crtfc_key": settings.DART_API_KEY,
        "corp_code": corp_code,
        "pblntf_ty": "I",
        "page_count": 5,
    }
    data = await _dart_get("list.json", params)

    if data.get("status") != "000" or not data.get("list"):
        return f"'{corp_name}'의 공모가격 정보를 찾을 수 없습니다. 아직 증권신고서가 제출되지 않았을 수 있습니다."

    items = data.get("list", [])
    results = [f"'{corp_name}' 관련 IPO 공시 목록:"]
    for item in items:
        results.append(
            f"- [{item.get('report_nm', '')}] 접수일: {item.get('rcept_dt', '')} "
            f"(접수번호: {item.get('rcept_no', '')})"
        )
    results.append("\n※ 상세 공모가격은 해당 증권신고서 본문에서 확인할 수 있습니다.")
    return "\n".join(results)


@tool
async def naver_search(query: str) -> str:
    """네이버에서 최신 뉴스를 검색합니다. IPO, 공모주 관련 최신 소식을 찾을 때 유용합니다.

    Args:
        query: 검색할 키워드 (예: "삼성전자 IPO", "공모주 청약 일정")
    """
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
async def get_stock_price(company_name: str) -> str:
    """한국 주식의 현재가, 등락률, 거래량 등 실시간 시세 정보를 조회합니다.

    Args:
        company_name: 조회할 한국 회사명 (예: "삼성전자", "카카오", "네이버")
    """
    import yfinance as yf

    # 회사명 → 종목코드 매핑 (corpCode.xml 캐시 활용)
    corps = await _load_corp_codes()
    stock_code = None
    corp_name_found = None

    for c in corps:
        if c["stock_code"] and (c["corp_name"] == company_name or company_name in c["corp_name"]):
            stock_code = c["stock_code"]
            corp_name_found = c["corp_name"]
            break

    if not stock_code:
        return f"'{company_name}'의 종목코드를 찾을 수 없습니다. 상장된 기업명을 확인해주세요."

    ticker_symbol = f"{stock_code}.KS"

    try:
        ticker = yf.Ticker(ticker_symbol)
        info = ticker.info

        if not info or "currentPrice" not in info:
            ticker_symbol = f"{stock_code}.KQ"
            ticker = yf.Ticker(ticker_symbol)
            info = ticker.info

        if not info or "currentPrice" not in info:
            return f"'{company_name}'({stock_code})의 시세 정보를 가져올 수 없습니다."

        return _format_stock_info(info, corp_name_found, stock_code, "원")

    except Exception as e:
        return f"주가 조회 중 오류 발생: {type(e).__name__}"


@tool
async def get_global_stock_price(ticker_or_name: str) -> str:
    """해외 주식(미국, 일본, 홍콩 등)의 현재가, 등락률, 거래량 등 실시간 시세를 조회합니다.

    Args:
        ticker_or_name: 종목 티커 또는 회사명 (예: "AAPL", "TSLA", "애플", "테슬라", "NVDA", "7203.T")
    """
    import yfinance as yf

    # 자주 검색되는 해외 주식 한글명 → 티커 매핑
    name_to_ticker = {
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

    # 한글명이면 티커로 변환, 아니면 그대로 사용
    ticker_symbol = name_to_ticker.get(ticker_or_name, ticker_or_name).upper()

    try:
        ticker = yf.Ticker(ticker_symbol)
        info = ticker.info

        if not info or "currentPrice" not in info:
            return f"'{ticker_or_name}' (티커: {ticker_symbol})의 시세 정보를 가져올 수 없습니다. 정확한 티커 심볼을 확인해주세요."

        currency = info.get("currency", "USD")
        currency_symbol = {"USD": "$", "JPY": "¥", "HKD": "HK$", "EUR": "€", "GBP": "£"}.get(currency, currency)
        display_name = info.get("shortName", ticker_or_name)

        return _format_stock_info(info, display_name, ticker_symbol, currency_symbol)

    except Exception as e:
        return f"해외 주가 조회 중 오류 발생: {type(e).__name__}"


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
