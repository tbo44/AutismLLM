/* Manual review never triggers delivery; sending remains a separate action. */
async function testAttemptRequest(path, options) {
  var token = _getSavedToken() || window.prompt(
    'Enter the crawl admin token (ADMIN_CRAWL_TOKEN) to review test-email attempts:'
  );
  token = (token || '').trim();
  if (!token) { throw new Error('Admin token required.'); }
  options = options || {};
  options.headers = { 'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json' };
  var response = await fetch(path, options);
  var body = await response.json();
  if (!response.ok) {
    if (response.status === 401 || response.status === 403) {
      try { sessionStorage.removeItem(_TOKEN_KEY); } catch (e) {}
      _crawlToken = '';
    }
    throw new Error(body.detail || 'Could not review attempts.');
  }
  return body;
}

var _testAttemptHistoryOffset = 0;
var _testAttemptHistoryLoading = false;

async function loadTestAlertHistory(offset) {
  if (_testAttemptHistoryLoading) { return; }
  _testAttemptHistoryLoading = true;
  var msg = document.getElementById('testAttemptHistoryMsg');
  var rows = document.getElementById('testAttemptHistoryRows');
  var refresh = document.getElementById('testAttemptHistoryRefresh');
  var previous = document.getElementById('testAttemptHistoryPrevious');
  var next = document.getElementById('testAttemptHistoryNext');
  refresh.disabled = previous.disabled = next.disabled = true;
  rows.textContent = '';
  msg.textContent = 'Loading archived history…';
  try {
    var body = await testAttemptRequest('/admin/alerts/test/attempts/history?limit=25&offset=' +
      Math.max(0, offset || 0));
    _testAttemptHistoryOffset = body.offset;
    var outcomes = { delivered: 'Email found', not_found: 'Email not found', unknown: 'Still uncertain' };
    body.attempts.forEach(function (attempt) {
      var row = document.createElement('tr');
      var resolved = new Date(attempt.resolved_at * 1000).toISOString();
      [attempt.key, outcomes[attempt.resolution] || attempt.resolution,
        resolved.replace('T', ' ').replace('Z', ' UTC')].forEach(function (text) {
        var cell = document.createElement('td');
        cell.textContent = text;
        row.appendChild(cell);
      });
      rows.appendChild(row);
    });
    msg.textContent = body.attempts.length
      ? 'Showing ' + (body.offset + 1) + '–' + (body.offset + body.attempts.length) +
        ' of ' + body.total + ' archived attempts. Original keys remain permanently protected.'
      : (body.total ? 'No archived attempts on this page. Use Previous or refresh history.'
        : 'No archived test-email attempts.');
    previous.disabled = body.offset === 0;
    next.disabled = body.offset + body.limit >= body.total;
  } catch (error) {
    msg.textContent = error.message;
  } finally {
    refresh.disabled = false;
    _testAttemptHistoryLoading = false;
  }
}

async function loadTestAlertAttempts() {
  var msg = document.getElementById('testAttemptReviewMsg');
  var rows = document.getElementById('testAttemptRows');
  msg.textContent = 'Loading attempts…';
  rows.textContent = '';
  try {
    var body = await testAttemptRequest('/admin/alerts/test/attempts');
    msg.textContent = body.attempts.length + ' unresolved; ' + body.retained + '/' +
      body.limit + ' retained attempts; ' + body.archived + ' archived keys permanently protected.';
    body.attempts.forEach(function (attempt) {
      var row = document.createElement('tr');
      [attempt.key, attempt.state].forEach(function (text) {
        var cell = document.createElement('td');
        cell.textContent = text;
        row.appendChild(cell);
      });
      var reviewCell = document.createElement('td');
      var select = document.createElement('select');
      select.setAttribute('aria-label', 'Inbox review for ' + attempt.key);
      [['unknown', 'Still uncertain'], ['delivered', 'Email found'], ['not_found', 'Email not found']].forEach(function (item) {
        var option = document.createElement('option');
        option.value = item[0]; option.textContent = item[1]; select.appendChild(option);
      });
      reviewCell.appendChild(select); row.appendChild(reviewCell);
      var actionCell = document.createElement('td');
      var button = document.createElement('button');
      button.className = 'test-alert-btn'; button.textContent = 'Resolve / archive';
      button.onclick = async function () {
        if (!window.confirm("Confirm you checked the recipient's inbox. Archive this attempt as: " +
            select.options[select.selectedIndex].text + '? No email will be sent. The original key stays permanently protected. A pending send may still finish; any new attempt could cause duplicate delivery.')) { return; }
        button.disabled = true;
        try {
          await testAttemptRequest('/admin/alerts/test/attempts/' + encodeURIComponent(attempt.key) + '/resolve', {
            method: 'POST', body: JSON.stringify({ inbox_checked: true, resolution: select.value })
          });
          await loadTestAlertAttempts();
        } catch (error) {
          msg.textContent = error.message; button.disabled = false;
        }
      };
      actionCell.appendChild(button); row.appendChild(actionCell); rows.appendChild(row);
    });
  } catch (error) { msg.textContent = error.message; }
}
