'use client';
import React, { useState } from 'react';
import dynamic from 'next/dynamic';
import { Button, Input } from 'antd';

const MonacoEditor = dynamic(() => import('react-monaco-editor').then(m => m.default).catch(() => () => null as any), { ssr: false } as any);

export default function RunPage() {
  const [jsonText, setJsonText] = useState('{"note":"Editable JSON Studio demo"}');
  const [reason, setReason] = useState('');
  return (
    <div>
      <h2>Run</h2>
      <div style={{ marginBottom: 8 }}>
        <Input.TextArea value={reason} onChange={e => setReason(e.target.value)} placeholder="Reason required for edits" />
      </div>
      <div style={{ height: 400, border: '1px solid #eee' }}>
        {MonacoEditor ? <MonacoEditor language="json" value={jsonText} onChange={setJsonText} options={{ minimap: { enabled: false } }} /> : <pre>{jsonText}</pre>}
      </div>
      <Button style={{ marginTop: 8 }} disabled={!reason}>Save Draft</Button>
    </div>
  );
}
