import { api } from '../api.js';

export async function renderEdges(view, setMsg) {
  const nodes = await api('/api/nodes');
  const edges = await api('/api/edges');
  const opts = (nodes.items || []).map((n) => `<option value="${n.id}">${n.node_code}</option>`).join('');
  view.innerHTML = `
    <section class="card">
      <h2>边关系</h2>
      <div class="row">
        <label>边号<input id="code" value="E-NEW-1" /></label>
        <label>上游<select id="up">${opts}</select></label>
        <label>下游<select id="down">${opts}</select></label>
        <label>变换<input id="kind" value="直抽" /></label>
        <button class="primary" id="create" type="button">创建边</button>
      </div>
    </section>
    <section class="wall" style="margin-top:1rem">
      ${(edges.items || []).map((e) => `<article class="card">
        <h3>${e.edge_code}</h3>
        <span class="tag">${e.status}</span>
        ${e.draft_source ? '<span class="tag tag-draft" data-thick="draft_source">草稿来源</span>' : ''}
        ${e.confirmed ? `<span class="tag tag-fate" data-thick="fate">${e.fate_label}</span>` : ''}
        <p>${e.upstream_code} → ${e.downstream_code}</p>
        <p class="muted">${e.transform_kind} · ${e.upstream_table} → ${e.downstream_table}</p>
        <div class="row">
          <button data-pub="${e.id}" class="primary" type="button" ${e.status !== '草稿' ? 'disabled' : ''}>发布</button>
          <button data-void="${e.id}" type="button" ${e.status === '已作废' ? 'disabled' : ''}>作废</button>
          ${e.status === '草稿' && !e.confirmed ? `
          <button data-seal="${e.id}" type="button">确认封草稿</button>
          <button data-allow="${e.id}" class="primary" type="button">确认准发布</button>` : ''}
        </div>
      </article>`).join('') || '<p class="muted">暂无边</p>'}
    </section>`;
  document.getElementById('create').onclick = async () => {
    try {
      await api('/api/edges', {
        method: 'POST',
        body: JSON.stringify({
          edge_code: document.getElementById('code').value,
          upstream_id: Number(document.getElementById('up').value),
          downstream_id: Number(document.getElementById('down').value),
          transform_kind: document.getElementById('kind').value,
        }),
      });
      setMsg('边已创建（草稿来源，未确认前不进可达集合）');
      await renderEdges(view, setMsg);
    } catch (e) { setMsg(e.message, true); }
  };
  view.querySelectorAll('[data-pub]').forEach((btn) => {
    btn.onclick = async () => {
      try {
        await api('/api/edges/' + btn.dataset.pub + '/publish', { method: 'POST', body: '{}' });
        setMsg('边已发布');
        await renderEdges(view, setMsg);
      } catch (e) { setMsg(e.message, true); }
    };
  });
  view.querySelectorAll('[data-void]').forEach((btn) => {
    btn.onclick = async () => {
      try {
        await api('/api/edges/' + btn.dataset.void + '/void', { method: 'POST', body: '{}' });
        setMsg('边已作废');
        await renderEdges(view, setMsg);
      } catch (e) { setMsg(e.message, true); }
    };
  });
  const confirm = async (id, fate, label) => {
    try {
      const r = await api('/api/edges/' + id + '/confirm', {
        method: 'POST',
        body: JSON.stringify({ fate, note: label }),
      });
      setMsg('确认整笔完成：' + r.item.edge_code + ' → ' + r.item.fate);
      await renderEdges(view, setMsg);
    } catch (e) { setMsg(e.message, true); }
  };
  view.querySelectorAll('[data-seal]').forEach((btn) => {
    btn.onclick = () => confirm(btn.dataset.seal, '封草稿', '确认封草稿');
  });
  view.querySelectorAll('[data-allow]').forEach((btn) => {
    btn.onclick = () => confirm(btn.dataset.allow, '准发布', '确认准发布');
  });
}
