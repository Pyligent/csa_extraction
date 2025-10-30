'use client';
import React, { useEffect, useState } from 'react';
import { Button, Select, Switch } from 'antd';

export default function IngestPage() {
  const apiBase = process.env.NEXT_PUBLIC_API_BASE || '';
  const [files, setFiles] = useState<string[]>([]);
  const [selected, setSelected] = useState<string | undefined>();
  const [withLLM, setWithLLM] = useState(false);
  const [result, setResult] = useState<any>(null);

  useEffect(() => {
    fetch(`${apiBase}/api/data/list`).then(r => r.json()).then(d => setFiles(d.files || [])).catch(() => setFiles([]));
  }, [apiBase]);

  const runFromData = async () => {
    if (!selected) return;
    const fd = new FormData();
    fd.append('path_in_data', selected);
    const ing = await fetch(`${apiBase}/api/csa/ingest`, { method: 'POST', body: fd }).then(r => r.json());
    const fd2 = new FormData();
    fd2.append('path', ing.path);
    fd2.append('with_llm', String(withLLM));
    const res = await fetch(`${apiBase}/api/extract`, { method: 'POST', body: fd2 }).then(r => r.json());
    setResult(res);
  };

  return (
    <div>
      <h2>Ingest</h2>
      <div style={{ display: 'flex', gap: 12, alignItems: 'center' }}>
        <Select
          style={{ width: 320 }}
          placeholder="Pick file from /data"
          options={files.map(f => ({ label: f, value: f }))}
          onChange={v => setSelected(v)}
        />
        <span>With LLM</span>
        <Switch checked={withLLM} onChange={setWithLLM} />
        <Button type="primary" onClick={runFromData} disabled={!selected}>Run</Button>
      </div>
      <pre style={{ marginTop: 16 }}>{JSON.stringify(result, null, 2)}</pre>
    </div>
  );
}
