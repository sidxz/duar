import { defineConfig } from 'tsup'

export default defineConfig({
  entry: ['src/index.ts', 'src/middleware.ts', 'src/server.ts', 'src/authz-middleware.ts', 'src/proxy.ts'],
  format: ['esm', 'cjs'],
  dts: true,
  clean: true,
  external: ['next', 'next/server', 'react', '@duar-auth/js', '@duar-auth/react', 'jose'],
})
