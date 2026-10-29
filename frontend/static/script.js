// ===== 글로벌 상태 =====
const state = {
  currentLandmark: null,
  chatHistory: [],
  isBotTyping: false,
};

// ===== 상수 문자열 (한국어) =====
const CONSTANTS = {
  botGreeting: "안녕하세요! 한국의 명소, 맛집, 조선시대 왕들에 대해 물어보세요.",
  restaurantError: "현재 어디계신가요?\n홈 화면에서 이미지를 업로드 해주세요!",
  cvError: "죄송합니다. 문화재를 찾을 수 없습니다.\n빠른 시일 내에 업로드하겠습니다.",
  chatError: "죄송합니다. 응답을 가져올 수 없습니다.\n잠시 후 다시 시도해주세요."
};

// ===== 앱 초기화 =====
document.addEventListener("DOMContentLoaded", () => {
  initApp();
});

async function initApp() {
  await showLoadingScreen();
  setupMainInteractions();
}

async function showLoadingScreen() {
  setActiveScreen("loading");

  const phoneFrame = document.querySelector(".phone-frame");
  const topTextEl = document.getElementById("loading-top-text");
  const bottomTextEl = document.getElementById("loading-bottom-text");
  const loadingBar = document.querySelector(".loading-bar-fill");
  const initialLoader = document.getElementById("initial-loader");

  if (phoneFrame) phoneFrame.classList.remove("visible");

  if (loadingBar) {
    loadingBar.classList.remove("running");
    loadingBar.style.width = "0%";
    void loadingBar.offsetWidth;
  }

  if (topTextEl) topTextEl.innerHTML = "";
  if (bottomTextEl) bottomTextEl.innerHTML = "";

  if (initialLoader) initialLoader.style.display = "block";

  try {
    const response = await fetch("/loading_quote");
    if (!response.ok) throw new Error("Failed to fetch quote");

    const data = await response.json();
    const quoteData = data.agent_response;

    if (quoteData && quoteData.reign_order) {
      const topHtml = `
        <span class="king-title">제 ${quoteData.reign_order} ${quoteData.name}</span>
        <span class="king-mbti" style="margin-left: 2px;">${quoteData.mbti}</span>
      `;
      const bottomHtml = `
        <div class="quote-divider"></div>
        <div class="king-quote">"${quoteData.quote}"</div>
      `;

      if (topTextEl) topTextEl.innerHTML = topHtml;
      if (bottomTextEl) bottomTextEl.innerHTML = bottomHtml;

    } else {
      throw new Error("Invalid quote data.");
    }

    if (initialLoader) initialLoader.style.display = "none";

    setTimeout(() => {
      if (phoneFrame) phoneFrame.classList.add("visible");
      if (loadingBar) loadingBar.classList.add("running");
    }, 100);

    setTimeout(() => {
      setActiveScreen("main");
      addBotMessage(CONSTANTS.botGreeting, 'bot', false);
    }, 10100);

  } catch (e) {
    console.error("Failed to load quote:", e);
    if (topTextEl) topTextEl.textContent = "접속 중...";

    if (initialLoader) initialLoader.style.display = "none";

    setTimeout(() => {
      if (phoneFrame) phoneFrame.classList.add("visible");
      if (loadingBar) loadingBar.classList.add("running");
    }, 100);

    setTimeout(() => {
      setActiveScreen("main");
    }, 10100);
  }
}

function startAppTimer(loadingBarElement) {
  if (loadingBarElement) {
    loadingBarElement.classList.add("running");
  }
  setTimeout(() => {
    setActiveScreen("main");
    addBotMessage(CONSTANTS.botGreeting, 'bot', false);
  }, 10000);
}

function setActiveScreen(name) {
  const screens = document.querySelectorAll(".screen");
  screens.forEach((s) => s.classList.remove("active"));
  const screen = document.getElementById(`screen-${name}`);
  if (screen) screen.classList.add("active");

  document.querySelectorAll(".bottom-nav .nav-item").forEach((btn) => {
    const target = btn.getAttribute("data-target");
    btn.classList.toggle("active", target === name);
  });
}

// ===== 메인 인터랙션 =====
function setupMainInteractions() {
  setupNav();
  setupImageUpload();
  setupChatbot();
}

// ===== 네비게이션 =====
function setupNav() {
  const navButtons = document.querySelectorAll(".bottom-nav .nav-item");
  navButtons.forEach((btn) => {
    btn.addEventListener("click", async () => {
      const target = btn.getAttribute("data-target");
      if (!target) return;
      if (target === "restaurants") {
        await showRestaurantScreen();
      } else {
        setActiveScreen(target);
      }
    });
  });
}

// ===== 이미지 업로드 =====
function setupImageUpload() {
  const input = document.getElementById("image-input");
  const preview = document.getElementById("image-preview");
  const instruction = document.getElementById("upload-instruction");

  const titleEl = document.getElementById("desc-title");
  const textEl = document.getElementById("desc-text");
  const placeLabel = document.getElementById("place-label-text");

  const sillokCard = document.getElementById("sillok-card");
  const sillokTextEl = document.getElementById("sillok-text");

  if (!input) return;

  input.addEventListener("change", async (e) => {
    const file = e.target.files[0];
    if (!file) return;

    const reader = new FileReader();
    reader.onload = () => {
      preview.src = reader.result;
      preview.style.display = "block";
      if (instruction) instruction.style.display = "none";
    };
    reader.readAsDataURL(file);

    titleEl.textContent = "이미지 분석 중...";
    textEl.innerHTML = '<div class="spinner" style="margin: 20px auto;"></div>';
    placeLabel.textContent = "장소";

    if (sillokCard) sillokCard.style.display = "none";

    const formData = new FormData();
    formData.append("file", file);

    try {
      const response = await fetch("/predict_image", {
        method: "POST",
        body: formData,
      });

      if (!response.ok) {
        throw new Error("분석 실패");
      }

      const result = await response.json();

      state.currentLandmark = result.cv_label;
      placeLabel.textContent = `${state.currentLandmark}`;
      titleEl.textContent = `${state.currentLandmark}에 오신 것을 환영합니다.`;

      const desc = result.description || "";
      const sillok = result.sillok_summary || "내용이 없습니다.";

      textEl.innerHTML = desc;

      if (sillokCard) {
        sillokCard.style.display = "block";
        sillokTextEl.innerHTML = sillok;
      }

    } catch (err) {
      state.currentLandmark = null;
      placeLabel.textContent = "장소";
      titleEl.textContent = "오류 발생";
      textEl.textContent = err.message;
      if (sillokCard) sillokCard.style.display = "none";
    } finally {
      input.value = null;
    }
  });
}

// ===== 맛집 화면 =====
async function showRestaurantScreen() {
  setActiveScreen("restaurants");
  const header = document.getElementById("restaurants-header");
  const list = document.getElementById("restaurants-list");
  if (!header || !list) return;

  if (!state.currentLandmark) {
    header.textContent = "";
    list.innerHTML = `
      <div class="empty-state">
        <img src="/static/images/camera.png" class="empty-icon" alt="카메라">
        <h3>아직 장소 정보가 없어요</h3>
        <p>
          홈 화면에서 사진을 업로드하시면<br>
          주변 맛집을 추천해 드립니다!
        </p>
        <button class="btn-primary" onclick="document.querySelector('[data-target=main]').click()">
          홈으로 가서 사진 올리기
        </button>
      </div>
    `;
    return;
  }

  header.innerHTML = `<span class="location-badge">📍 ${state.currentLandmark} 인근 맛집입니다!</span>`;
  list.innerHTML = '<div class="spinner" style="margin: 20px auto;"></div>';

  try {
    const response = await fetch(`/restaurants/${state.currentLandmark}`);
    if (!response.ok) throw new Error("Failed to fetch restaurants");
    const data = await response.json();
    const restaurants = data.agent_response;
    list.innerHTML = "";

    if (!restaurants || restaurants.length === 0) {
      list.innerHTML = `<p>맛집 정보가 없습니다.</p>`;
      return;
    }

    restaurants.forEach((r) => {
      const card = document.createElement("div");
      card.className = "restaurant-card";

      const img = document.createElement("img");
      img.className = "restaurant-thumb";
      img.src = r.menu_image ? `/static/images/${r.menu_image}` : 'https://via.placeholder.com/64?text=Food';
      img.alt = r.name;
      img.onerror = () => { img.src = 'https://via.placeholder.com/64?text=Food'; };

      const info = document.createElement("div");
      info.className = "restaurant-info";
      info.innerHTML = `
        <h3>${r.name || '정보 없음'}</h3>
        <p><b>위치 :</b> ${r.location || r.address || '정보 없음'}</p>
        <p><b>대표메뉴 :</b> ${r.signature || r.signature_menu || '정보 없음'}</p>
        <p><b>메뉴 :</b> ${r.menu && r.menu.length > 0 ? r.menu.join(', ') : '정보 없음'}</p>
        <p><b>리뷰 :</b> ${r.reviews && r.reviews.length > 0 ? r.reviews.join(' ') : '정보 없음'}</p>
      `;
      card.appendChild(img);
      card.appendChild(info);
      list.appendChild(card);
    });
  } catch (e) {
    console.error("Error:", e);
    list.innerHTML = `<p>맛집 정보를 불러오는 중 오류가 발생했습니다.</p>`;
  }
}

// ===== 챗봇 =====
function setupChatbot() {
  const form = document.getElementById("chat-form");
  const input = document.getElementById("chat-input");
  if (!form || !input) return;

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (state.isBotTyping) return;

    const text = input.value.trim();
    if (!text) return;

    addUserMessage(text);
    input.value = "";
    state.isBotTyping = true;

    const loadingBubble = addBotMessage("...", 'bot', true);

    try {
      const response = await fetch("/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          input: text,
          chat_history: state.chatHistory,
        }),
      });
      if (!response.ok) throw new Error("Server error");

      const data = await response.json();
      const reply = data.output;

      loadingBubble.textContent = reply;
      state.chatHistory = data.chat_history;
      scrollChatToBottom();
    } catch (e) {
      console.error("Chat Error:", e);
      loadingBubble.textContent = CONSTANTS.chatError;
      state.chatHistory.push({ role: 'ai', content: '오류가 발생했습니다.' });
      scrollChatToBottom();
    } finally {
      state.isBotTyping = false;
    }
  });
}

function addUserMessage(text) {
  const chatWindow = document.getElementById("chat-window");
  if (!chatWindow) return;

  const bubble = document.createElement("div");
  bubble.className = "chat-bubble user";
  bubble.textContent = text;

  chatWindow.appendChild(bubble);
  scrollChatToBottom();
}

function scrollChatToBottom() {
  const scroller = document.querySelector(".chatbot-content");
  if (!scroller) return;
  scroller.scrollTo({ top: scroller.scrollHeight, behavior: "smooth" });
}

function addBotMessage(text, role = 'bot', isLoading = false) {
  const chatWindow = document.getElementById("chat-window");
  if (!chatWindow) return null;

  const wrapper = document.createElement("div");
  const meta = document.createElement("div");
  meta.className = "chat-meta";
  meta.textContent = "📜 실록이";

  const bubble = document.createElement("div");
  bubble.className = "chat-bubble bot";

  if (isLoading) {
    bubble.innerHTML = '<div class="spinner" style="width: 20px; height: 20px; border-width: 2px; margin: 0 auto;"></div>';
  } else {
    bubble.textContent = text;
  }

  wrapper.appendChild(meta);
  wrapper.appendChild(bubble);
  chatWindow.appendChild(wrapper);
  scrollChatToBottom();

  if (state.chatHistory.length === 0 && !isLoading) {
    state.chatHistory.push({ role: 'ai', content: text });
  }

  return bubble;
}