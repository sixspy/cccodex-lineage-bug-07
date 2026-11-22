import { api } from '../api.js';

export async function renderSettings(view, setMsg) {
  const j = await api('/api/settings');
  view.innerHTML = `
    <section class="card">
      <h2>台参数</h2>
      <div class="wall">
        ${(j.items || []).map((s) => `<article class="card">
          <h3>${s.label}</h3>
          <p class="muted">${s.key}</p>
          <div class="row">
            <input data-k="${s.key}" value="${s.value}" />
            <button data-save="${s.key}" class="primary" type="button">保存</button>
          </div>
        </article>`).join('')}
      </div>
    </section>`;
  view.querySelectorAll('[data-save]').forEach((btn) => {
    btn.onclick = async () => {
      try {
        const input = view.querySelector('input[data-k="' + btn.dataset.save + '"]');
        await api('/api/settings', {
          method: 'POST',
          body: JSON.stringify({ key: btn.dataset.save, value: input.value }),
        });
        setMsg('参数已保存');
        await renderSettings(view, setMsg);
      } catch (e) { setMsg(e.message, true); }
    };
  });

  try {
    const dash = await api('/api/draft_publish/dash');
    const gate = dash.gate || { allowed: false, violations: [] };
    view.innerHTML += `<section class="card" style="margin-top:1rem">
      <h3>草稿确认</h3>
      <div class="row">
        <span class="tag">台面芯片：${dash.chip}</span>
        <span class="tag">库内真相：${dash.truth}</span>
        <span class="tag tag-draft" data-thick="unpublished">未发布边：${dash.unpublished_count}</span>
        <span class="tag ${gate.allowed ? 'tag-ok' : 'tag-block'}">解析闸：${gate.allowed ? '放行' : '拦截'}</span>
      </div>
      <p class="muted">命运行 ${dash.durable_count} · 对齐差 ${dash.panel_pack_mismatch}</p>
      ${gate.violations && gate.violations.length ? `<p class="err" data-thick="gate-violation">${gate.violations.join('；')}</p>` : ''}
      ${gate.warnings && gate.warnings.length ? `<p class="muted">${gate.warnings.join('；')}</p>` : ''}
      <div class="wall" style="margin-top:.75rem">
        ${(dash.edges || []).map((e) => `<article class="card">
          <h4>${e.edge_code}</h4>
          <div class="row">
            <span class="tag">${e.status}</span>
            ${e.draft_source ? '<span class="tag tag-draft" data-thick="draft_source">草稿来源</span>' : ''}
            ${e.confirmed ? `<span class="tag tag-fate" data-thick="fate">命运：${e.fate}</span>` : '<span class="tag">未确认</span>'}
          </div>
          ${e.draft_source && !e.confirmed ? `
          <div class="row" style="margin-top:.5rem">
            <button data-seal="${e.edge_id}" type="button">确认封草稿</button>
            <button data-allow="${e.edge_id}" class="primary" type="button">确认准发布</button>
          </div>` : ''}
        </article>`).join('')}
      </div>
      <div class="row" style="margin-top:.75rem">
        <button id="half_draft_publish" type="button">半修对齐（只碰展示）</button>
      </div>
    </section>`;
    const confirm = async (edgeId, fate) => {
      try {
        const r = await api('/api/draft_publish/commit', {
          method: 'POST',
          body: JSON.stringify({ edge_id: Number(edgeId), fate }),
        });
        setMsg(r.message || '确认整笔完成');
        await renderSettings(view, setMsg);
      } catch (e) { setMsg(e.message, true); }
    };
    view.querySelectorAll('[data-seal]').forEach((btn) => {
      btn.onclick = () => confirm(btn.dataset.seal, '封草稿');
    });
    view.querySelectorAll('[data-allow]').forEach((btn) => {
      btn.onclick = () => confirm(btn.dataset.allow, '准发布');
    });
    document.getElementById('half_draft_publish').onclick = async () => {
      try {
        const r = await api('/api/draft_publish/half', { method: 'POST', body: '{}' });
        setMsg('半修后芯片 ' + r.panel_chip + ' / 真相 ' + r.truth + ' / 命运行 ' + r.pack_count + '（无命运行仍不对齐）');
        await renderSettings(view, setMsg);
      } catch (e) { setMsg(e.message, true); }
    };
  } catch (e) { /* optional surface */ }

}
