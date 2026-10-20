(function () {
  var devPorts = ['3000', '5173', '5500', '5501', '8080', '8081', '8888'];
  var isLocal = location.protocol === 'file:' ||
    location.hostname === 'localhost' || location.hostname === '127.0.0.1';
  var API;
  if (isLocal && devPorts.indexOf(location.port) !== -1) API = 'http://localhost:8000/api';
  else if (location.hostname.endsWith('.vercel.app')) API = 'https://shopytics.onrender.com/api';
  else API = '/api';
  window.APP_CONFIG = { API_BASE_URL: API };
})();