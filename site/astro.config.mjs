// @ts-check
import { defineConfig } from 'astro/config';
import starlight from '@astrojs/starlight';
import starlightLinksValidator from 'starlight-links-validator';

export default defineConfig({
	site: 'https://liyux3.github.io',
	base: '/scholar-mcp',
	integrations: [
		starlight({
			title: 'Scholar MCP',
			description:
				'Find papers from natural-language questions, follow citations, read the primary text and keep a local research library.',
			logo: {
				light: './src/assets/symbol-light.svg',
				dark: './src/assets/symbol-dark.svg',
				alt: '',
			},
			favicon: '/favicon.svg',
			disable404Route: true,
			defaultLocale: 'root',
			locales: {
				root: { label: 'English', lang: 'en' },
				'zh-cn': { label: '简体中文', lang: 'zh-CN' },
			},
			social: [
				{ icon: 'github', label: 'GitHub', href: 'https://github.com/Liyux3/scholar-mcp' },
			],
			editLink: { baseUrl: 'https://github.com/Liyux3/scholar-mcp/edit/main/site/' },
			customCss: ['@fontsource-variable/inter', './src/styles/custom.css'],
			components: {
				PageTitle: './src/components/PageTitle.astro',
				ThemeSelect: './src/components/ThemeToggle.astro',
			},
			expressiveCode: {
				themes: ['github-dark-default', 'github-light'],
				defaultProps: { frame: 'code', wrap: true },
				styleOverrides: {
					borderRadius: '10px',
					borderWidth: '1px',
					borderColor: 'var(--sc-border)',
					codeBackground: 'var(--sc-code-bg)',
					codeFontSize: '0.8125rem',
					codeLineHeight: '1.7',
					codePaddingBlock: '0.9rem',
					codePaddingInline: '1.1rem',
					uiFontFamily: 'var(--__sl-font)',
					uiFontSize: '0.8125rem',
					frames: {
						frameBoxShadowCssValue: 'none',
						editorTabBarBackground: 'var(--sc-surface-2)',
						editorTabBarBorderBottomColor: 'var(--sc-border)',
						editorActiveTabBackground: 'var(--sc-code-bg)',
						editorActiveTabForeground: 'var(--sc-muted)',
						editorActiveTabBorderColor: 'transparent',
						editorActiveTabIndicatorTopColor: 'transparent',
						editorActiveTabIndicatorBottomColor: 'transparent',
						editorTabBorderRadius: '0',
						inlineButtonBorder: 'var(--sc-border)',
						inlineButtonForeground: 'var(--sc-muted)',
						tooltipSuccessBackground: 'var(--sc-ink)',
						tooltipSuccessForeground: 'var(--sc-bg)',
					},
				},
			},
			plugins: [starlightLinksValidator()],
			sidebar: [
				{
					label: 'Get started', translations: { 'zh-CN': '入门' },
					items: [
						{ label: 'Introduction', translations: { 'zh-CN': '简介' }, slug: 'index' },
						{ label: 'Quickstart', translations: { 'zh-CN': '快速开始' }, slug: 'quickstart' },
						{ label: 'Install in your client', translations: { 'zh-CN': '接入客户端' }, slug: 'clients' },
					],
				},
				{
					label: 'Guides', translations: { 'zh-CN': '指南' },
					items: [
						{ label: 'Research workflows', translations: { 'zh-CN': '研究工作流' }, slug: 'research-workflows' },
						{ label: 'Paper library', translations: { 'zh-CN': '文献库' }, slug: 'paper-library' },
						{ label: 'Scientific data', translations: { 'zh-CN': '科学数据' }, slug: 'scientific-data' },
						{ label: 'Institutional access', translations: { 'zh-CN': '机构访问' }, slug: 'institutional-access' },
					],
				},
				{
					label: 'Reference', translations: { 'zh-CN': '参考' },
					items: [
						{ label: 'MCP tools', translations: { 'zh-CN': 'MCP 工具' }, slug: 'tools' },
						{ label: 'Command line', translations: { 'zh-CN': '命令行' }, slug: 'cli' },
						{ label: 'Python API', slug: 'python' },
						{ label: 'Configuration', translations: { 'zh-CN': '配置' }, slug: 'configuration' },
						{ label: 'Sources', translations: { 'zh-CN': '数据源' }, slug: 'sources' },
						{ label: 'Architecture', translations: { 'zh-CN': '架构' }, slug: 'architecture' },
						{ label: 'Benchmarks', translations: { 'zh-CN': '基准测试' }, slug: 'benchmarks' },
					],
				},
				{
					label: 'Resources', translations: { 'zh-CN': '资源' },
					items: [
						{ label: 'FAQ', translations: { 'zh-CN': '常见问题' }, slug: 'faq' },
						{ label: 'Changelog', translations: { 'zh-CN': '更新日志' }, slug: 'changelog' },
					],
				},
			],
		}),
	],
});
