import { useEffect, useRef } from "react";
import { Renderer, Camera, Geometry, Program, Mesh } from "ogl";
import "./Particles.css";

const vertex = `attribute vec3 position;attribute vec4 random;attribute vec3 color;uniform mat4 modelMatrix;uniform mat4 viewMatrix;uniform mat4 projectionMatrix;uniform float uTime;uniform float uSpread;uniform float uBaseSize;uniform float uSizeRandomness;varying vec4 vRandom;varying vec3 vColor;void main(){vRandom=random;vColor=color;vec3 pos=position*uSpread;pos.z*=10.0;vec4 p=modelMatrix*vec4(pos,1.0);p.x+=sin(uTime*random.z+6.28*random.w)*mix(.1,1.5,random.x);p.y+=sin(uTime*random.y+6.28*random.x)*mix(.1,1.5,random.w);p.z+=sin(uTime*random.w+6.28*random.y)*mix(.1,1.5,random.z);vec4 mv=viewMatrix*p;gl_PointSize=uSizeRandomness==0.0?uBaseSize:(uBaseSize*(1.0+uSizeRandomness*(random.x-.5)))/length(mv.xyz);gl_Position=projectionMatrix*mv;}`;
const fragment = `precision highp float;uniform float uTime;uniform float uAlphaParticles;varying vec4 vRandom;varying vec3 vColor;void main(){vec2 uv=gl_PointCoord.xy;float d=length(uv-vec2(.5));if(uAlphaParticles<.5){if(d>.5)discard;gl_FragColor=vec4(vColor+.2*sin(uv.yxx+uTime+vRandom.y*6.28),1.0);}else{float c=smoothstep(.5,.4,d)*.8;gl_FragColor=vec4(vColor+.2*sin(uv.yxx+uTime+vRandom.y*6.28),c);}}`;
const rgb = hex => { const n=parseInt(hex.replace("#", ""),16); return [((n>>16)&255)/255,((n>>8)&255)/255,(n&255)/255]; };

export default function Particles({particleCount=200,particleSpread=10,speed=.1,particleColors=["#ffffff"],moveParticlesOnHover=false,particleHoverFactor=1,alphaParticles=false,particleBaseSize=100,sizeRandomness=1,cameraDistance=20,disableRotation=false,pixelRatio=1,className=""}) {
  const ref=useRef(null), mouse=useRef({x:0,y:0});
  useEffect(()=>{const el=ref.current;if(!el)return;const renderer=new Renderer({dpr:pixelRatio,depth:false,alpha:true}),gl=renderer.gl;el.appendChild(gl.canvas);gl.clearColor(0,0,0,0);const camera=new Camera(gl,{fov:15});camera.position.set(0,0,cameraDistance);
    const resize=()=>{renderer.setSize(el.clientWidth,el.clientHeight);camera.perspective({aspect:gl.canvas.width/gl.canvas.height});};
    const move=e=>{const r=el.getBoundingClientRect();mouse.current={x:((e.clientX-r.left)/r.width)*2-1,y:-(((e.clientY-r.top)/r.height)*2-1)};};
    window.addEventListener("resize",resize);if(moveParticlesOnHover)el.addEventListener("mousemove",move);resize();
    const pos=new Float32Array(particleCount*3),rand=new Float32Array(particleCount*4),cols=new Float32Array(particleCount*3);
    for(let i=0;i<particleCount;i++){let x,y,z,l;do{x=Math.random()*2-1;y=Math.random()*2-1;z=Math.random()*2-1;l=x*x+y*y+z*z;}while(l>1||l===0);const r=Math.cbrt(Math.random());pos.set([x*r,y*r,z*r],i*3);rand.set([Math.random(),Math.random(),Math.random(),Math.random()],i*4);cols.set(rgb(particleColors[i%particleColors.length]),i*3);}
    const geometry=new Geometry(gl,{position:{size:3,data:pos},random:{size:4,data:rand},color:{size:3,data:cols}}),program=new Program(gl,{vertex,fragment,uniforms:{uTime:{value:0},uSpread:{value:particleSpread},uBaseSize:{value:particleBaseSize*pixelRatio},uSizeRandomness:{value:sizeRandomness},uAlphaParticles:{value:alphaParticles?1:0}},transparent:true,depthTest:false}),mesh=new Mesh(gl,{mode:gl.POINTS,geometry,program});let raf,last=performance.now(),elapsed=0;
    const draw=t=>{raf=requestAnimationFrame(draw);if(document.hidden)return;elapsed+=(t-last)*speed;last=t;program.uniforms.uTime.value=elapsed*.001;mesh.position.x=moveParticlesOnHover?-mouse.current.x*particleHoverFactor:0;mesh.position.y=moveParticlesOnHover?-mouse.current.y*particleHoverFactor:0;if(!disableRotation){mesh.rotation.x=Math.sin(elapsed*.0002)*.1;mesh.rotation.y=Math.cos(elapsed*.0005)*.15;mesh.rotation.z+=.01*speed;}renderer.render({scene:mesh,camera});};raf=requestAnimationFrame(draw);
    return()=>{cancelAnimationFrame(raf);window.removeEventListener("resize",resize);el.removeEventListener("mousemove",move);if(el.contains(gl.canvas))el.removeChild(gl.canvas);gl.getExtension("WEBGL_lose_context")?.loseContext();};
  },[particleCount,particleSpread,speed,particleColors,moveParticlesOnHover,particleHoverFactor,alphaParticles,particleBaseSize,sizeRandomness,cameraDistance,disableRotation,pixelRatio]);
  return <div ref={ref} className={`particles-container ${className}`}/>;
}
