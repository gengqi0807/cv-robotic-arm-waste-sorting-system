/* Camera capture stays in the browser; identity is returned by the robot API. */
(() => {
  const select = (id) => document.getElementById(id);
  const message = select('login-message');
  const faceForm = select('face-form');
  const passwordForm = select('password-form');
  const preview = select('face-preview');
  const open = select('camera-open');
  const close = select('camera-close');
  const upload = select('face-upload');
  const status = select('camera-status');
  const success = select('face-success');
  const robotMode = faceForm?.dataset?.cameraSource === 'robot';
  const robotPreview = select('robot-face-preview');
  let stream;
  let generation = 0;
  let requestController;
  let timer;
  let redirectTimer;
  let recognized = false;
  let startedAt;
  const roles = {operator: '操作员', manager: '班组长', maintainer: '维护员'};
  const homes = {operator: '/tasks', manager: '/stats', maintainer: '/maintain'};

  function showStatus(text) {
    if (status) { status.textContent = text; status.hidden = !text; }
  }
  function showError(error) {
    message.className = 'notice error';
    message.textContent = error.message;
    message.hidden = false;
  }
  function stopCamera() {
    generation += 1;
    clearTimeout(timer);
    requestController?.abort();
    stream?.getTracks().forEach((track) => track.stop());
    stream = undefined;
    if (preview) { preview.srcObject = null; preview.hidden = true; }
    if (robotPreview) { robotPreview.hidden = true; robotPreview.removeAttribute('src'); }
    if (open) { open.hidden = recognized; open.disabled = recognized; }
    if (close) close.hidden = true;
  }
  function accepted(result, byFace) {
    recognized = true;
    stopCamera();
    const target = byFace ? success : message;
    target.className = 'notice success';
    target.textContent = `${byFace ? '人脸识别成功' : '登录成功'}：${result.name || result.operator_id} · ${result.role_name || roles[result.role] || result.role}，正在进入系统…`;
    target.hidden = false;
    showStatus('身份已确认');
    passwordForm.querySelector('button[type="submit"]').disabled = true;
    if (upload) upload.disabled = true;
    redirectTimer = setTimeout(() => window.location.assign(homes[result.role] || '/records'), 1400);
  }
  async function login(endpoint, options, controller) {
    const timeout = setTimeout(() => controller.abort(), 15000);
    try {
      const response = await fetch(endpoint, {method: 'POST', ...options, signal: controller.signal});
      const result = await response.json();
      return {response, result};
    } finally { clearTimeout(timeout); }
  }
  async function encode(source, width, height) {
    const canvas = document.createElement('canvas');
    const scale = Math.min(1, 960 / Math.max(width, height));
    canvas.width = Math.max(1, Math.round(width * scale));
    canvas.height = Math.max(1, Math.round(height * scale));
    canvas.getContext('2d').drawImage(source, 0, 0, canvas.width, canvas.height);
    const photo = await new Promise((resolve) => canvas.toBlob(resolve, 'image/jpeg', 0.85));
    if (!photo || photo.size > 2 * 1024 * 1024) throw new Error('图片处理失败，请重试。');
    const body = new FormData();
    body.append('image', photo, 'face.jpg');
    return body;
  }
  async function recognize(current) {
    if (generation !== current || (!robotMode && !stream) || recognized) return;
    if (Date.now() - startedAt >= 30000) {
      stopCamera();
      showStatus('暂未识别到登记人员，已关闭摄像头。请调整光线后重新开启，或使用密码登录。');
      return;
    }
    if (!robotMode && (!preview.videoWidth || !preview.videoHeight)) {
      timer = setTimeout(() => recognize(current), 300);
      return;
    }
    const controller = new AbortController();
    requestController = controller;
    showStatus('正在识别，请正对镜头…');
    try {
      const body = robotMode ? undefined : await encode(preview, preview.videoWidth, preview.videoHeight);
      if (generation !== current) return;
      if (body) body.append('terminal_id', 'web-camera-auto');
      if (robotMode) {
        robotPreview.src = '/api/auth/face/preview?t=' + Date.now();
        robotPreview.hidden = false;
      }
      const {response, result} = await login(robotMode ? '/api/auth/face/device' : '/api/auth/face/image', {body}, controller);
      if (generation !== current) return;
      if (response.ok && result.authorized) { accepted(result, true); return; }
      if (response.status === 409) {
        showStatus(result.error || '等待机械臂到达人脸观察位…');
      } else if (response.status === 422 || response.status === 401) {
        showStatus(response.status === 422 ? '请让单张人脸清晰出现在镜头中，正在继续识别…' : '暂未匹配到登记人员，请正对镜头，正在重试…');
      } else {
        throw new Error(result.error || result.message || '人脸识别服务暂不可用，请重试。');
      }
    } catch (error) {
      if (generation !== current) return;
      stopCamera();
      showError(error.name === 'AbortError' ? new Error('识别请求超时，请重新开启识别。') : error);
      showStatus('自动识别已停止');
      return;
    }
    if (generation === current && !recognized) timer = setTimeout(() => recognize(current), 1000);
  }
  async function startCamera() {
    if (recognized) return;
    stopCamera();
    message.hidden = true;
    success.hidden = true;
    const current = generation;
    if (robotMode) {
      startedAt = Date.now();
      close.hidden = false;
      open.hidden = true;
      showStatus('等待机械臂到达人脸观察位，请面向机械臂摄像头…');
      timer = setTimeout(() => recognize(current), 1000);
      return;
    }
    if (!navigator.mediaDevices?.getUserMedia) {
      showError(new Error('请使用 HTTPS 或本机 localhost/127.0.0.1 地址访问摄像头。'));
      showStatus('浏览器无法访问摄像头');
      return;
    }
    open.disabled = true;
    showStatus('正在开启摄像头，首次使用请允许浏览器授权…');
    try {
      const camera = await navigator.mediaDevices.getUserMedia({audio: false,
        video: {facingMode: 'user', width: {ideal: 960}, height: {ideal: 720}}});
      if (generation !== current) { camera.getTracks().forEach((track) => track.stop()); return; }
      stream = camera;
      preview.srcObject = camera;
      preview.hidden = false;
      close.hidden = false;
      open.hidden = true;
      await preview.play();
      if (generation !== current) return;
      for (const track of camera.getVideoTracks()) track.addEventListener('ended', () => {
        if (stream !== camera) return;
        stopCamera();
        showStatus('摄像头已断开，请重新开启。');
      });
      startedAt = Date.now();
      showStatus('请正对镜头，即将自动识别…');
      timer = setTimeout(() => recognize(current), 1000);
    } catch (error) {
      if (generation !== current) return;
      stopCamera();
      const reasons = {NotAllowedError: '请允许浏览器使用摄像头，再点击重新开启自动识别。',
        NotFoundError: '未找到摄像头，请检查连接。', NotReadableError: '摄像头无法读取，请关闭占用摄像头的应用。'};
      showError(new Error(reasons[error.name] || '摄像头开启失败，请重试。'));
      showStatus('摄像头未开启');
    }
  }
  passwordForm.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (recognized) return;
    stopCamera();
    const button = passwordForm.querySelector('button[type="submit"]');
    if (button.disabled) return;
    button.disabled = true;
    const controller = new AbortController();
    requestController = controller;
    try {
      const {response, result} = await login('/api/auth/password', {
        headers: {'Content-Type': 'application/json'}, body: JSON.stringify(Object.fromEntries(new FormData(passwordForm)))}, controller);
      if (!response.ok || !result.authorized) throw new Error(result.error || '登录失败，请检查凭据。');
      accepted(result, false);
    } catch (error) { showError(error); }
    finally { button.disabled = recognized; }
  });
  if (faceForm) {
    open.addEventListener('click', startCamera);
    close.addEventListener('click', () => { stopCamera(); showStatus('自动识别已关闭'); });
    faceForm.addEventListener('submit', async (event) => {
      event.preventDefault();
      if (recognized || upload.disabled) return;
      stopCamera();
      const current = generation;
      const controller = new AbortController();
      requestController = controller;
      upload.disabled = open.disabled = true;
      let bitmap;
      try {
        const file = faceForm.elements.image.files[0];
        if (!file) throw new Error('请先选择照片。');
        bitmap = await createImageBitmap(file);
        const body = await encode(bitmap, bitmap.width, bitmap.height);
        body.append('terminal_id', 'web-image');
        const {response, result} = await login('/api/auth/face/image', {body}, controller);
        if (generation !== current) return;
        if (!response.ok || !result.authorized) throw new Error(result.message || result.error || '未匹配到登记人员。');
        accepted(result, true);
      } catch (error) { if (generation === current) showError(error); }
      finally { bitmap?.close(); upload.disabled = open.disabled = recognized; }
    });
    startCamera();
  }
  window.addEventListener('pagehide', () => { stopCamera(); clearTimeout(redirectTimer); });
  document.addEventListener('visibilitychange', () => {
    if (document.hidden && !recognized) { stopCamera(); showStatus('页面已隐藏，自动识别已停止。返回后可重新开启。'); }
  });
})();
