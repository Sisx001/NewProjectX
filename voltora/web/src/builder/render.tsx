// Entry point resolved by `@/builder/render` and by the storefront's build-time page loader.
export { DocumentView, NodeView, PageView, registerRenderer, hasRenderer, nodeAttrs, compileCss, isVisible, resolveBindings } from "./render/index";
export type { NodeProps, Renderer } from "./render/index";
