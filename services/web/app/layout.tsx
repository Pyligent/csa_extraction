'use client';
import React from 'react';
import { ProLayout } from '@ant-design/pro-layout';
import Link from 'next/link';
import 'antd/dist/reset.css';

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <ProLayout
          title="CSA Extraction Pro"
          route={{
            path: '/',
            routes: [
              { path: '/', name: 'Dashboard' },
              { path: '/ingest', name: 'Ingest' },
              { path: '/run', name: 'Runs' },
            ],
          }}
          menuItemRender={(item, dom) => (
            <Link href={item.path || '/'}>{dom}</Link>
          )}
        >
          <div style={{ padding: 24 }}>{children}</div>
        </ProLayout>
      </body>
    </html>
  );
}
