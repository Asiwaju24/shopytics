// API location. When the page is served by nginx/Docker (same origin) the API is at /api.
// When you open the frontend from a simple dev server on another port, it talks to Django on :8000.
(function () {
  var devPorts = ['3000', '5173', '5500', '5501', '8080', '8081', '8888'];
  var onDevServer = location.protocol === 'file:' || devPorts.indexOf(location.port) !== -1;
  window.APP_CONFIG = {
    API_BASE_URL: onDevServer ? 'http://localhost:8000/api' : '/api',
  };
})();
