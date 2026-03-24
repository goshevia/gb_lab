let sessionId = null;
let remainingMessages = 0;

const startCard = document.getElementById('startCard');
const chatCard = document.getElementById('chatCard');
const finishCard = document.getElementById('finishCard');
const resultCard = document.getElementById('resultCard');
const startError = document.getElementById('startError');
const chatError = document.getElementById('chatError');
const finishError = document.getElementById('finishError');
const chatBox = document.getElementById('chatBox');
const remainingInfo = document.getElementById('remainingInfo');

function setRemainingInfo() {
  remainingInfo.textContent = `Осталось реплик: ${remainingMessages}`;
}

function addMessage(role, text) {
  const el = document.createElement('div');
  el.className = `message ${role}`;
  el.innerHTML = `<div class="role">${role === 'assistant' ? 'Собеседник' : 'Вы'}</div><div>${escapeHtml(text).replace(/\n/g, '<br>')}</div>`;
  chatBox.appendChild(el);
  chatBox.scrollTop = chatBox.scrollHeight;
}

function escapeHtml(text) {
  const div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
}

async function postJson(url, payload) {
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
  });
  const data = await res.json();
  if (!res.ok) {
    throw new Error(data.error || 'Неизвестная ошибка');
  }
  return data;
}

document.getElementById('startBtn').addEventListener('click', async () => {
  startError.textContent = '';
  try {
    const participantName = document.getElementById('participantName').value.trim();
    const accessCode = document.getElementById('accessCode').value.trim();
    const data = await postJson('/api/start', { participant_name: participantName, access_code: accessCode });
    sessionId = data.session_id;
    remainingMessages = data.max_user_messages;
    startCard.classList.add('hidden');
    chatCard.classList.remove('hidden');
    addMessage('assistant', data.message);
    setRemainingInfo();
  } catch (err) {
    startError.textContent = err.message;
  }
});

document.getElementById('sendBtn').addEventListener('click', async () => {
  chatError.textContent = '';
  const input = document.getElementById('messageInput');
  const text = input.value.trim();
  if (!text) return;

  input.value = '';
  addMessage('user', text);

  try {
    const data = await postJson('/api/message', { session_id: sessionId, message: text });
    addMessage('assistant', data.reply);
    remainingMessages = data.remaining_messages;
    setRemainingInfo();
    if (remainingMessages <= 0) {
      openFinishScreen();
    }
  } catch (err) {
    chatError.textContent = err.message;
  }
});

document.getElementById('finishBtn').addEventListener('click', openFinishScreen);

function openFinishScreen() {
  chatCard.classList.add('hidden');
  finishCard.classList.remove('hidden');
}

document.getElementById('finalizeBtn').addEventListener('click', async () => {
  finishError.textContent = '';
  try {
    const guessedType = document.getElementById('guessedType').value.trim();
    const data = await postJson('/api/finish', { session_id: sessionId, guessed_type: guessedType });
    finishCard.classList.add('hidden');
    resultCard.classList.remove('hidden');

    const strengths = (data.strengths || []).map(item => `<li>${escapeHtml(item)}</li>`).join('');
    const improvements = (data.improvements || []).map(item => `<li>${escapeHtml(item)}</li>`).join('');

    document.getElementById('resultContent').innerHTML = `
      <div class="score">${data.score_total ?? 0} / 100</div>
      <p>${escapeHtml(data.summary || '')}</p>
      <h3>Что было хорошо</h3>
      <ul>${strengths}</ul>
      <h3>Что можно улучшить</h3>
      <ul>${improvements}</ul>
    `;
  } catch (err) {
    finishError.textContent = err.message;
  }
});
