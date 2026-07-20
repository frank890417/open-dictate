const pad = document.querySelector('.voice-pad');
const state = document.querySelector('.voice-state');
const output = document.querySelector('[data-transcript]');

const phrases = [
  '語音不出境，文字不失控。',
  '寧可漏改，不可錯改。',
  '每個建議，都由你決定是否採用。',
  '在本機聽懂，也在本機進化。'
];

let phraseIndex = 0;
let recording = false;

function startRecording(event) {
  if (event.type === 'keydown' && event.code !== 'Space') return;
  if (event.type === 'keydown') event.preventDefault();
  if (recording) return;
  if (event.pointerId != null && pad.setPointerCapture) pad.setPointerCapture(event.pointerId);
  recording = true;
  pad.classList.add('is-recording');
  pad.setAttribute('aria-pressed', 'true');
  state.textContent = '正在聽寫…放開即轉錄';
  output.textContent = '••••••••••';
}

function stopRecording(event) {
  if (event.type === 'keyup' && event.code !== 'Space') return;
  if (!recording) return;
  recording = false;
  pad.classList.remove('is-recording');
  pad.setAttribute('aria-pressed', 'false');
  state.textContent = '按住開始說話';
  phraseIndex = (phraseIndex + 1) % phrases.length;
  output.textContent = phrases[phraseIndex];
}

pad.addEventListener('pointerdown', startRecording);
pad.addEventListener('pointerup', stopRecording);
pad.addEventListener('pointercancel', stopRecording);
pad.addEventListener('keydown', startRecording);
pad.addEventListener('keyup', stopRecording);
