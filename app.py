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

load_dotenv()

NOTION_TOKEN = os.getenv("NOTION_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# --- 1. 설정 및 초기화 ---
nest_asyncio.apply()

st.set_page_config(page_title="Notion x Gemini (Fixed)", layout="wide")
st.title("🤖 Notion Assistant")


# --- 2. Notion MCP 서버 설정 ---
npx_path = shutil.which("npx")
if not npx_path:
    st.error("❌ 'npx' 명령어를 찾을 수 없습니다.")
    st.stop()

server_params = StdioServerParameters(
    command=npx_path,
    args=["-y", "@notionhq/notion-mcp-server"],
    env={**os.environ, "NOTION_TOKEN": NOTION_TOKEN}
)

# --- 3. 도구(Tool) 함수 정의 ---

async def _mcp_tool_call(tool_name: str, arguments: dict):
    """MCP 서버의 특정 도구를 호출하는 내부 함수"""
    print(f"DEBUG: {tool_name} 호출 중... 인자: {arguments}")
    try:
        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                
                # 타임아웃 60초 설정
                result = await asyncio.wait_for(
                    session.call_tool(tool_name, arguments=arguments),
                    timeout=60.0
                )
                
                # 결과 텍스트 추출
                if hasattr(result, 'content') and result.content:
                    text_content = []
                    for c in result.content:
                        if hasattr(c, 'text'):
                            text_content.append(c.text)
                    return "\n\n".join(text_content)
                return str(result)

    except Exception as e:
        return f"⚠️ 에러 발생 ({tool_name}): {str(e)}"

# [Tool 1] 검색 기능 (강화됨: 날짜와 ID 정보 포함)
def query_notion(query: str) -> str:
    """
    Notion에서 문서를 검색합니다. 제목, 수정일, Page ID를 반환합니다.
    """
    result = asyncio.run(_mcp_tool_call("API-post-search", {"query": query}))
    
    # MCP 결과에서 유용한 정보만 정제해서 LLM에게 줍니다.
    if hasattr(result, 'content') and result.content:
        parsed_results = []
        try:
            # MCP 서버가 보통 JSON 문자열을 줍니다. 파싱 시도.
            # 텍스트 형태의 리스트라면 그대로 처리
            for item in result.content:
                if hasattr(item, 'text'):
                    # 원본 JSON 데이터가 너무 길어서 LLM이 헷갈려하니 요약해줍니다.
                    # 실제로는 여기서 JSON 파싱을 해서 예쁘게 주는게 좋지만, 
                    # Notion MCP의 응답 형태가 복잡하므로 텍스트 전체를 넘깁니다.
                    # 다만, Gemini가 잘 이해하도록 프롬프트로 제어합니다.
                    return item.text 
        except:
            pass
        return str(result.content)
    return "검색 결과가 없습니다."

# [Tool 2] 내용 읽기 기능 (새로 추가됨!)
def get_page_content(page_id: str) -> str:
    """
    특정 Page ID에 해당하는 문서의 실제 내용을 읽어옵니다.
    검색 결과에서 찾은 ID를 이 도구에 입력하세요.
    """
    # API-get-block-children 도구는 block_id(여기선 page_id)를 받아 하위 블록(텍스트)을 줍니다.
    result = asyncio.run(_mcp_tool_call("API-get-block-children", {"block_id": page_id}))
    
    if hasattr(result, 'content') and result.content:
        text_content = []
        for c in result.content:
            if hasattr(c, 'text'):
                text_content.append(c.text)
        return "\n".join(text_content)
    return "내용을 읽을 수 없습니다."

# --- 4. Gemini 클라이언트 설정 ---
client = genai.Client(api_key=GEMINI_API_KEY)

tools_list = [query_notion]

config = types.GenerateContentConfig(
    tools=tools_list,
    automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=False),
    system_instruction="""
    당신은 Notion 전문 비서입니다. 
    사용자의 질문에 답하기 위해 'query_notion' 도구를 적극적으로 사용하세요.
    검색 결과가 JSON이나 복잡한 형태라면, 사용자가 보기 좋게 요약해서 설명해주세요.
    """
)

if "chat_session" not in st.session_state:
    st.session_state.chat_session = client.chats.create(
        model="gemini-3-pro-preview",
        config=config
    )

# --- 5. UI 및 채팅 ---
if "messages" not in st.session_state:
    st.session_state.messages = []

for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

if prompt := st.chat_input("노션에서 무엇을 찾아드릴까요?"):
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        with st.spinner("Notion 검색 중..."):
            try:
                response = st.session_state.chat_session.send_message(prompt)
                
                final_text = "응답 없음"
                if response.text:
                    final_text = response.text
                elif response.candidates and response.candidates[0].content.parts:
                    for part in response.candidates[0].content.parts:
                        if part.text:
                            final_text = part.text
                            break
                
                st.markdown(final_text)
                st.session_state.messages.append({"role": "assistant", "content": final_text})
            
            except Exception as e:
                st.error("오류가 발생했습니다.")
                with st.expander("상세 에러 로그"):
                    st.code(traceback.format_exc())