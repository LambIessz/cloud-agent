import { fileURLToPath, URL } from 'node:url'

import { defineConfig, type PluginOption } from 'vite'
import vue from '@vitejs/plugin-vue'
import vueDevTools from 'vite-plugin-vue-devtools'

// https://vite.dev/config/
export default defineConfig(({ command }) => {
  const plugins: PluginOption[] = [vue()]
  const backendProxyTarget = process.env.VITE_BACKEND_URL || 'http://localhost:5000'

  if (command === 'serve') {
    plugins.push(vueDevTools())
  }

  return {
    plugins,
    resolve: {
      alias: {
        '@': fileURLToPath(new URL('./src', import.meta.url))
      },
    },
    build: {
      rolldownOptions: {
        output: {
          codeSplitting: {
            groups: [
              {
                name: 'element-plus',
                test: /node_modules[\\/](@element-plus[\\/]icons-vue|element-plus)[\\/]/,
                priority: 30,
              },
              {
                name: 'vue-vendor',
                test: /node_modules[\\/](@vue|vue)[\\/]/,
                priority: 20,
              },
              {
                name: 'markdown',
                test: /node_modules[\\/]marked[\\/]/,
                priority: 10,
              },
            ],
          },
        },
      },
    },
    server: {
      proxy: {
        '/api': {
          target: backendProxyTarget,
          changeOrigin: true,
        }
      }
    }
  }
})
