function toggleMenu() {
  document.getElementById('chat-menu').classList.toggle('hidden');
}

function openPanel(id) {
  document.getElementById('chat-menu').classList.add('hidden');
  ['agent-panel', 'bot-panel'].forEach(p => {
    if (p !== id) document.getElementById(p).classList.add('hidden');
  });
  document.getElementById(id).classList.remove('hidden');
}

function closePanel(id) {
  document.getElementById(id).classList.add('hidden');
}

function getCookie(name) {
  const m = document.cookie.match('(^|;)\\s*' + name + '\\s*=\\s*([^;]+)');
  return m ? m.pop() : '';
}

function appendBubble(panelId, text, cls) {
  const body = document.getElementById(panelId + '-body');
  const div = document.createElement('div');
  div.className = 'chat-bubble ' + cls;
  div.textContent = text;
  body.appendChild(div);
  body.scrollTop = body.scrollHeight;
}

// ---------------------------------------------------------------------
// Tool-call rendering: turns a raw {name, input, output} tool call into a
// readable formatted bubble (a title + paragraph/list/mini-table) instead
// of a dumped "toolName({...json...}) -> {...json...}" line. Mirrors
// agents/presentation.py's server-side formatting so an agent's result
// looks the same whether it came from a dashboard button or from chat.
// Built with createElement/textContent (never innerHTML with interpolated
// data) so nothing in a tool's output can be interpreted as markup.
// ---------------------------------------------------------------------

const TOOL_ACTION_LABELS = {
  explain_topic: 'Explanation', generate_practice_quiz: 'Practice quiz',
  summarize_material: 'Summary', generate_summary_from_material: 'Material summary',
  generate_course_summary: 'Course summary', generate_quiz_from_material: 'Quiz generated',
  generate_quiz_for_topic: 'Quiz generated', create_study_plan: 'Study plan created',
  create_practice_task: 'Practice task added', mark_task_done: 'Task updated',
  delete_practice_task: 'Task deleted', recommend_quiz: 'Quiz recommendation',
  add_question_to_quiz: 'Question added', create_quiz: 'Quiz created',
  cancel_quiz: 'Quiz cancelled', grade_essay_response: 'Response graded',
  review_study_plan: 'Study plan reviewed', get_at_risk_students: 'At-risk report',
  send_announcement: 'Announcement sent', upload_material: 'Material uploaded',
  delete_material: 'Material deleted', get_performance: 'Performance',
  get_course_analytics: 'Analytics', get_student_performance: 'Student report',
};

function toolActionLabel(name) {
  return TOOL_ACTION_LABELS[name] || name.replace(/_/g, ' ');
}

function humanizeKey(key) {
  const words = key.replace(/_/g, ' ');
  return words.charAt(0).toUpperCase() + words.slice(1);
}

function stringifyToolValue(v) {
  if (v === null || v === undefined || v === '') return '—';
  if (typeof v === 'boolean') return v ? 'Yes' : 'No';
  if (Array.isArray(v)) return v.map(stringifyToolValue).join(', ');
  return String(v);
}

function appendKvRows(container, obj) {
  const list = document.createElement('div');
  list.className = 'tool-kv';
  Object.keys(obj).forEach(function (key) {
    if (key === 'ok' || key.endsWith('_id')) return;
    const value = obj[key];
    const row = document.createElement('div');
    row.className = 'tool-kv-row';
    const label = document.createElement('span');
    label.className = 'tool-kv-label';
    label.textContent = humanizeKey(key) + ': ';
    row.appendChild(label);

    if (Array.isArray(value) && value.length && typeof value[0] === 'object' && value[0] !== null) {
      value.forEach(function (item) {
        const sub = document.createElement('div');
        sub.className = 'tool-kv-sub';
        sub.textContent = Object.keys(item)
          .filter(function (k) { return k !== 'ok' && !k.endsWith('_id'); })
          .map(function (k) { return humanizeKey(k) + ': ' + stringifyToolValue(item[k]); })
          .join(' · ');
        row.appendChild(sub);
      });
    } else {
      const val = document.createElement('span');
      val.className = 'tool-kv-value';
      val.textContent = stringifyToolValue(value);
      row.appendChild(val);
    }
    list.appendChild(row);
  });
  if (!list.children.length) {
    list.textContent = 'Done.';
  }
  container.appendChild(list);
}

function buildToolBubbleContent(tc) {
  const wrap = document.createElement('div');
  const output = tc.output || {};

  if (output.requires_confirmation) {
    wrap.classList.add('tool-warning');
    const title = document.createElement('div');
    title.className = 'tool-title';
    title.textContent = '⚠ Confirmation needed';
    const body = document.createElement('div');
    body.className = 'tool-body';
    body.textContent = output.message || 'This action needs to be confirmed before it runs.';
    wrap.appendChild(title);
    wrap.appendChild(body);
    return wrap;
  }
  if (output.ok === false) {
    wrap.classList.add('tool-error');
    const title = document.createElement('div');
    title.className = 'tool-title';
    title.textContent = '❌ ' + toolActionLabel(tc.name) + ' failed';
    const body = document.createElement('div');
    body.className = 'tool-body';
    body.textContent = output.error || 'Something went wrong.';
    wrap.appendChild(title);
    wrap.appendChild(body);
    return wrap;
  }

  const data = output.data || {};
  const title = document.createElement('div');
  title.className = 'tool-title';

  if (data.explanation) {
    title.textContent = '🎓 ' + (data.topic || 'Explanation');
    const body = document.createElement('div');
    body.className = 'tool-body';
    body.textContent = data.explanation;
    wrap.appendChild(title);
    wrap.appendChild(body);
    return wrap;
  }

  if (data.practice_questions) {
    title.textContent = '📝 Practice quiz' + (data.topic ? ' — ' + data.topic : '');
    wrap.appendChild(title);
    data.practice_questions.forEach(function (q, i) {
      const qBlock = document.createElement('div');
      qBlock.className = 'tool-question';
      const qText = document.createElement('div');
      qText.textContent = (i + 1) + '. ' + q.question;
      qBlock.appendChild(qText);
      if (q.correct_answer) {
        const ans = document.createElement('div');
        ans.className = 'tool-answer';
        ans.textContent = '✅ ' + q.correct_answer;
        qBlock.appendChild(ans);
      }
      wrap.appendChild(qBlock);
    });
    if (!data.practice_questions.length) {
      const body = document.createElement('div');
      body.className = 'tool-body';
      body.textContent = 'No practice questions were generated.';
      wrap.appendChild(body);
    }
    return wrap;
  }

  if (data.summary) {
    if (data.material) {
      title.textContent = '✨ Summary — ' + data.material;
    } else if (data.course) {
      title.textContent = '📊 ' + data.course + ' — AI summary';
    } else {
      title.textContent = '✨ Summary';
    }
    const body = document.createElement('div');
    body.className = 'tool-body';
    body.textContent = data.summary;
    wrap.appendChild(title);
    wrap.appendChild(body);
    return wrap;
  }

  title.textContent = '✅ ' + toolActionLabel(tc.name);
  wrap.appendChild(title);
  appendKvRows(wrap, data);
  return wrap;
}

function appendToolBubble(panelId, tc) {
  const body = document.getElementById(panelId + '-body');
  const div = document.createElement('div');
  div.className = 'chat-bubble tool';
  div.appendChild(buildToolBubbleContent(tc));
  body.appendChild(div);
  body.scrollTop = body.scrollHeight;
}

async function sendChat(evt, panelId) {
  evt.preventDefault();
  const form = evt.target;
  const input = form.querySelector('input[type=text]');
  const message = input.value;
  if (!message) return false;
  appendBubble(panelId, message, 'user');
  input.value = '';

  const endpoint = document.getElementById(panelId).dataset.endpoint;
  try {
    const res = await fetch(endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken') },
      body: JSON.stringify({ message }),
    });
    const data = await res.json();
    if (data.reply !== undefined) {
      appendBubble(panelId, data.reply, 'bot');
    }
    if (data.tool_calls && data.tool_calls.length) {
      data.tool_calls.forEach(tc => appendToolBubble(panelId, tc));
    }
  } catch (e) {
    appendBubble(panelId, 'Error talking to the server.', 'bot');
  }
  return false;
}

document.addEventListener('click', function (e) {
  const menu = document.getElementById('chat-menu');
  const fab = document.getElementById('chat-fab');
  if (!menu || menu.classList.contains('hidden')) return;
  if (!menu.contains(e.target) && e.target !== fab && !fab.contains(e.target)) {
    menu.classList.add('hidden');
  }
});
