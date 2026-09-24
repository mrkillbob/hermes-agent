declare module 'three' {
  export class Object3D {
    name: string
    children: Object3D[]
    rotation: { z: number }
    traverse(callback: (object: Object3D) => void): void
  }

  export class Scene extends Object3D {
    background: Color | null
    add(...objects: Object3D[]): this
  }

  export class Box3 {
    setFromObject(object: Object3D, precise?: boolean): this
    getCenter(target: Vector3): Vector3
    getSize(target: Vector3): Vector3
  }

  export class Color {
    constructor(color: number | string)
  }

  export class Vector2 {
    constructor(x?: number, y?: number)
    x: number
    y: number
  }

  export class Vector3 {
    constructor(x?: number, y?: number, z?: number)
    x: number
    y: number
    z: number
    set(x: number, y: number, z: number): this
  }

  export class PerspectiveCamera extends Object3D {
    constructor(fov?: number, aspect?: number, near?: number, far?: number)
    aspect: number
    near: number
    far: number
    position: Vector3
    lookAt(target: Vector3): void
    updateProjectionMatrix(): void
  }

  export class AmbientLight extends Object3D {
    constructor(color?: number | string, intensity?: number)
  }

  export class DirectionalLight extends Object3D {
    constructor(color?: number | string, intensity?: number)
    position: Vector3
  }

  export class WebGLRenderer {
    constructor(parameters?: { alpha?: boolean; antialias?: boolean; powerPreference?: string })
    domElement: HTMLCanvasElement
    setSize(width: number, height: number, updateStyle?: boolean): void
    setPixelRatio(value: number): void
    render(scene: Scene, camera: PerspectiveCamera): void
    dispose(): void
  }

  export class Raycaster {
    setFromCamera(coords: Vector2, camera: PerspectiveCamera): void
    intersectObjects(objects: Object3D[], recursive?: boolean): Array<{ object: Object3D }>
  }
}

declare module 'three/examples/jsm/loaders/GLTFLoader.js' {
  import type { Object3D, Scene } from 'three'

  export interface GLTF {
    scene: Scene & Object3D
  }

  export class GLTFLoader {
    load(
      url: string,
      onLoad: (gltf: GLTF) => void,
      onProgress?: ((event: ProgressEvent) => void) | undefined,
      onError?: ((event: unknown) => void) | undefined
    ): void
  }
}
