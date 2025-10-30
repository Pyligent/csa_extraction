'use client';
import React, { useEffect, useState } from 'react';

export default function Page() {
  const [status, setStatus] = useState<any>(null);
  useEffect(() => {
    fetch(`${process.env.NEXT_PUBLIC_API_BASE || ''}/healthz`).then(r => r.json()).then(setStatus).catch(() => setStatus({ error: true }));
  }, []);
  return (
    <div>
      <h2>Dashboard</h2>
      <pre>{JSON.stringify(status, null, 2)}</pre>
    </div>
  );
}
