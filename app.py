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

# [Tool 1] 검색 기능 (이름 수정됨: API-post-search)
def query_notion(query: str) -> str:
    """
    Notion 워크스페이스에서 키워드로 문서를 검색합니다.
    문서의 제목이나 내용을 찾을 때 사용합니다.
    """
    # PDF 분석 결과 [cite: 801, 1057]에 따라 'API-post-search'와 'query' 인자 사용
    return asyncio.run(_mcp_tool_call("API-post-search", {"query": query}))

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