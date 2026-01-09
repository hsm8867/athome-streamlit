import streamlit as st
import os
import asyncio
import nest_asyncio
import shutil
import traceback
import json
from google import genai
from google.genai import types
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from dotenv import load_dotenv

# .env 파일 로드
load_dotenv()

# --- 1. 필수 설정 및 초기화 ---
nest_asyncio.apply()

st.set_page_config(page_title="Notion AI Agent", layout="wide")
st.title("🤖 Notion Intelligent Agent")

# API 키 설정
NOTION_TOKEN = os.getenv("NOTION_TOKEN") or "ntn_여기에_토큰을_입력하세요"
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY") or "AIza_여기에_키를_입력하세요"

if "여기에" in NOTION_TOKEN or "여기에" in GEMINI_API_KEY:
    st.error("🚨 API 키가 설정되지 않았습니다!")
    st.stop()

# --- 2. 세션 초기화 버튼 ---
with st.sidebar:
    if st.button("🗑️ 대화 기록 & 세션 초기화", type="primary"):
        for key in list(st.session_state.keys()):
            del st.session_state[key]
        st.rerun()

# --- 3. Notion MCP 서버 설정 ---
npx_path = shutil.which("npx")
if not npx_path:
    st.error("❌ 'npx' 명령어를 찾을 수 없습니다.")
    st.stop()

server_params = StdioServerParameters(
    command=npx_path,
    args=["-y", "@notionhq/notion-mcp-server"],
    env={**os.environ, "NOTION_TOKEN": NOTION_TOKEN}
)

# --- 4. 헬퍼 함수: Notion JSON 파싱 (ID 포함 버전) ---
def parse_notion_blocks(data):
    """블록의 텍스트와 함께 'ID'도 노출하여 LLM이 파고들 수 있게 함"""
    text_lines = []
    
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except:
            return data

    blocks = data.get("results", []) if isinstance(data, dict) else data
    
    if not isinstance(blocks, list):
        return str(blocks)

    for block in blocks:
        if not isinstance(block, dict): continue
        
        b_type = block.get("type")
        b_id = block.get("id") # [중요] 블록 ID 추출
        has_children = block.get("has_children", False)
        
        content = block.get(b_type, {})
        rich_text = content.get("rich_text", [])
        
        line = ""
        for rt in rich_text:
            line += rt.get("plain_text", "")
            
        # 텍스트가 있거나 하위 블록이 있는 경우 출력
        if line or has_children:
            prefix = "- "
            if b_type == "toggle": prefix = "> "
            if b_type == "heading_1": prefix = "# "
            if b_type == "heading_2": prefix = "## "
            if b_type == "heading_3": prefix = "### "
            
            # [핵심] LLM이 볼 수 있게 ID와 하위블록 여부를 텍스트에 같이 적어줌
            info = f" (ID: {b_id}, 하위블록있음: {has_children})"
            text_lines.append(f"{prefix}{line}{info}")
            
    return "\n".join(text_lines) if text_lines else "(비어있는 블록)"

# --- 5. 도구(Tool) 함수 정의 ---

async def _mcp_tool_call(tool_name: str, arguments: dict):
    print(f"\n[DEBUG] {tool_name} 호출: {arguments}")
    try:
        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                
                result = await asyncio.wait_for(
                    session.call_tool(tool_name, arguments=arguments),
                    timeout=60.0
                )
                
                # 결과 처리 로직 분기
                if hasattr(result, 'content') and result.content:
                    raw_texts = []
                    for c in result.content:
                        if hasattr(c, 'text'):
                            raw_texts.append(c.text)
                    
                    full_json_str = "".join(raw_texts)

                    # [핵심 수정] 검색(Search) 결과는 파싱하지 않고 그대로 줌 (JSON 유지)
                    # 그래야 Gemini가 Page ID와 날짜를 정확히 뽑아낼 수 있음
                    if tool_name == "API-post-search":
                        return full_json_str
                    
                    # [핵심 수정] 내용 읽기(Block Children)일 때만 파싱함
                    if tool_name == "API-get-block-children":
                        return parse_notion_blocks(full_json_str)
                    
                    return full_json_str
                
                return str(result)

    except Exception as e:
        err_msg = f"ERROR: {str(e)}"
        print(err_msg)
        return err_msg

# [Tool 1] 검색
def query_notion(query: str) -> str:
    """Notion 문서를 검색합니다. (결과: JSON 형식의 Page 목록)"""
    return asyncio.run(_mcp_tool_call("API-post-search", {"query": query}))

# [Tool 2] 내용 읽기
def get_page_content(page_id: str) -> str:
    """Page ID를 받아 문서 내용을 읽어옵니다. (결과: 파싱된 텍스트)"""
    return asyncio.run(_mcp_tool_call("API-get-block-children", {"block_id": page_id}))

# --- 6. Gemini 모델 설정 ---
client = genai.Client(api_key=GEMINI_API_KEY)
tools_list = [query_notion, get_page_content]

sys_instruct = """
    당신은 Notion 문서 분석 에이전트입니다. 사용자의 질문에 답하기 위해 다음 전략을 구사하세요.

    1. **탐색 (Drill-Down) 전략**:
    - Notion 문서는 '블록' 안에 '블록'이 들어있는 트리 구조입니다.
    - `get_page_content`를 호출했을 때, 반환된 텍스트에 `> 1월 9일` 처럼 **토글(Toggle)**이나 **하위 페이지**가 보인다면, 
    - 사용자가 그 날짜의 내용을 물었을 때 **반드시 그 블록의 ID를 찾아내어 다시 `get_page_content`를 호출**해야 합니다.
    - **중요:** 한 번 읽어서 안 나오면, 포기하지 말고 하위 블록 ID로 계속 파고드세요.

    2. **프로세스**:
    Step 1: `query_notion`으로 전체 페이지(`AX팀 Daily Scrum`)를 찾는다.
    Step 2: `get_page_content`로 페이지의 최상위 블록들을 읽는다. (여기서 날짜별 토글들이 보일 것임)
    Step 3: **[핵심]** 사용자가 원하는 날짜(예: 1월 9일)의 블록 ID를 식별하여, 그 ID로 **다시** `get_page_content`를 호출한다.
    Step 4: 그렇게 해서 나온 상세 내용을 요약하여 답변한다.

    3. **제약 사항**:
    - 절대 "지원하지 않는다"라고 말하지 마세요. 도구를 연쇄적으로 사용(Chain of thought)하면 읽을 수 있습니다.
"""

# 세션 버전 업 (v6: 검색/읽기 로직 분리 적용)
if "chat_session_v6" not in st.session_state:
    print("✨ 새로운 세션(v6)이 시작되었습니다. (로직 분리됨)")
    st.session_state.chat_session_v6 = client.chats.create(
        model="gemini-2.0-flash-exp", 
        config=types.GenerateContentConfig(
            tools=tools_list,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=False),
            system_instruction=sys_instruct
        )
    )
    st.session_state.messages = []

# --- 7. UI ---
for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

if prompt := st.chat_input("질문 입력..."):
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        status = st.empty()
        status.markdown("🔄 Notion 검색 및 분석 중...")
        try:
            response = st.session_state.chat_session_v6.send_message(prompt)
            
            final_text = "⚠️ 응답 없음"
            if response.text:
                final_text = response.text
            elif response.candidates and response.candidates[0].content.parts:
                for part in response.candidates[0].content.parts:
                    if part.text:
                        final_text = part.text
                        break
            
            status.empty()
            st.markdown(final_text)
            st.session_state.messages.append({"role": "assistant", "content": final_text})
            
        except Exception as e:
            status.empty()
            st.error("오류 발생")
            st.code(traceback.format_exc())