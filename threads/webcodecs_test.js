(() => {
  'use strict';
  const W = 1080, H = 1920, FPS = 30;
  const canvas = document.getElementById('canvas');
  const ctx = canvas.getContext('2d', { alpha: false });
  const video = document.getElementById('source');
  const status = document.getElementById('status');
  let sourceUrl = null;

  const u8 = (...values) => new Uint8Array(values.flat());
  const concat = parts => {
    const size = parts.reduce((n, p) => n + p.length, 0), out = new Uint8Array(size);
    let offset = 0; for (const part of parts) { out.set(part, offset); offset += part.length; }
    return out;
  };
  const idBytes = id => {
    const bytes = []; while (id) { bytes.unshift(id & 255); id >>>= 8; } return u8(bytes);
  };
  const vint = value => {
    for (let width = 1; width <= 8; width++) {
      if (value < 2 ** (7 * width) - 1) {
        const out = new Uint8Array(width); let n = value;
        for (let i = width - 1; i >= 0; i--) { out[i] = n & 255; n = Math.floor(n / 256); }
        out[0] |= 1 << (8 - width); return out;
      }
    }
    throw new Error('EBML element quá lớn');
  };
  const uint = value => {
    const out = []; do { out.unshift(value & 255); value = Math.floor(value / 256); } while (value); return u8(out);
  };
  const str = value => new TextEncoder().encode(value);
  const f64 = value => { const b = new ArrayBuffer(8); new DataView(b).setFloat64(0, value); return new Uint8Array(b); };
  const el = (id, data) => concat([idBytes(id), vint(data.length), data]);
  const master = (id, children) => el(id, concat(children));

  class WebMMuxer {
    constructor(width, height, fps, durationMs) { this.width=width;this.height=height;this.fps=fps;this.durationMs=durationMs;this.frames=[]; }
    add(chunk) { const data = new Uint8Array(chunk.byteLength); chunk.copyTo(data); this.frames.push({data,time:Math.round(chunk.timestamp/1000),key:chunk.type==='key'}); }
    finish() {
      const ebml = master(0x1A45DFA3,[el(0x4286,uint(1)),el(0x42F7,uint(1)),el(0x42F2,uint(4)),el(0x42F3,uint(8)),el(0x4282,str('webm')),el(0x4287,uint(2)),el(0x4285,uint(2))]);
      const info = master(0x1549A966,[el(0x2AD7B1,uint(1000000)),el(0x4D80,str('Threads Browser Renderer')),el(0x5741,str('WebCodecs')),el(0x4489,f64(this.durationMs))]);
      const videoTrack = master(0xE0,[el(0xB0,uint(this.width)),el(0xBA,uint(this.height)),el(0x54B0,uint(this.width)),el(0x54BA,uint(this.height))]);
      const track = master(0xAE,[el(0xD7,uint(1)),el(0x73C5,uint(1)),el(0x83,uint(1)),el(0x86,str('V_VP8')),el(0x23E383,uint(Math.round(1e9/this.fps))),videoTrack]);
      const tracks = master(0x1654AE6B,[track]);
      const clusters=[]; let clusterStart=-1, blocks=[];
      const flush=()=>{if(clusterStart<0)return;clusters.push(master(0x1F43B675,[el(0xE7,uint(clusterStart)),...blocks]));blocks=[];};
      for(const frame of this.frames){
        if(clusterStart<0||frame.time-clusterStart>30000||(frame.key&&blocks.length>0)){flush();clusterStart=frame.time;}
        const rel=frame.time-clusterStart, header=u8(0x81,(rel>>8)&255,rel&255,frame.key?0x80:0x00);
        blocks.push(el(0xA3,concat([header,frame.data])));
      }
      flush();
      const segmentPayload=concat([info,tracks,...clusters]);
      return new Blob([ebml,idBytes(0x18538067),vint(segmentPayload.length),segmentPayload],{type:'video/webm'});
    }
  }

  function coverRect(srcW,srcH,dstW,dstH){const s=Math.max(dstW/srcW,dstH/srcH);const w=srcW*s,h=srcH*s;return{x:(dstW-w)/2,y:(dstH-h)/2,w,h};}
  function roundedRect(x,y,w,h,r){ctx.beginPath();ctx.roundRect(x,y,w,h,r);ctx.fill();}
  function wrapText(text,x,y,maxWidth,lineHeight,maxLines=8){
    const words=text.split(/\s+/);let line='',lines=[];
    for(const word of words){const test=line?line+' '+word:word;if(ctx.measureText(test).width>maxWidth&&line){lines.push(line);line=word;}else line=test;}
    if(line)lines.push(line);lines=lines.slice(0,maxLines);lines.forEach((v,i)=>ctx.fillText(v,x,y+i*lineHeight));return lines.length*lineHeight;
  }
  function drawFrame(progress=0){
    ctx.fillStyle='#050505';ctx.fillRect(0,0,W,H);
    if(video.readyState>=2){const r=coverRect(video.videoWidth,video.videoHeight,W,H);ctx.drawImage(video,r.x,r.y,r.w,r.h);}
    const x=70,y=500,w=940,p=42;
    ctx.fillStyle='rgba(255,255,255,.97)';roundedRect(x,y,w,530,34);
    ctx.fillStyle='#111';ctx.beginPath();ctx.arc(x+p+52,y+p+52,52,0,Math.PI*2);ctx.fill();
    ctx.fillStyle='#fff';ctx.font='700 44px -apple-system, BlinkMacSystemFont, sans-serif';ctx.textAlign='center';ctx.textBaseline='middle';ctx.fillText('T',x+p+52,y+p+54);
    ctx.textAlign='left';ctx.textBaseline='alphabetic';ctx.fillStyle='#111';ctx.font='700 40px -apple-system, BlinkMacSystemFont, sans-serif';ctx.fillText(document.getElementById('name').value||'Người dùng Threads',x+p+125,y+p+48);
    ctx.fillStyle='#777';ctx.font='32px -apple-system, BlinkMacSystemFont, sans-serif';ctx.fillText('vừa xong',x+p+125,y+p+91);
    ctx.fillStyle='#111';ctx.font='38px -apple-system, BlinkMacSystemFont, sans-serif';wrapText(document.getElementById('text').value,x+p,y+190,w-p*2,54,5);
    ctx.fillStyle='#555';ctx.font='31px -apple-system, BlinkMacSystemFont, sans-serif';ctx.fillText('♡  12,5K     ◯  328     ⟳  102     ↗  89',x+p,y+470);
    ctx.fillStyle='rgba(0,0,0,.55)';ctx.fillRect(0,H-14,W,14);ctx.fillStyle='#8b5cf6';ctx.fillRect(0,H-14,W*progress,14);
  }
  function seek(time){return new Promise((resolve,reject)=>{video.onseeked=()=>resolve();video.onerror=()=>reject(new Error('Không đọc được video'));video.currentTime=Math.min(time,Math.max(0,video.duration-.04));});}

  document.getElementById('video-file').onchange=async event=>{
    const file=event.target.files[0];if(!file)return;if(sourceUrl)URL.revokeObjectURL(sourceUrl);sourceUrl=URL.createObjectURL(file);video.src=sourceUrl;
    await new Promise((resolve,reject)=>{video.onloadedmetadata=resolve;video.onerror=()=>reject(new Error('Video không đọc được'));});
    await seek(0);drawFrame(0);status.textContent=`✓ ${video.videoWidth}×${video.videoHeight}, ${video.duration.toFixed(1)} giây. Sẵn sàng.`;
  };
  document.getElementById('snapshot').onclick=()=>drawFrame(0);
  document.getElementById('encode').onclick=async()=>{
    if(!sourceUrl)return alert('Hãy chọn video nền.');
    if(!window.VideoEncoder||!window.VideoFrame)return alert('Trình duyệt chưa hỗ trợ WebCodecs. Hãy dùng Chrome/Edge mới.');
    const button=document.getElementById('encode');button.disabled=true;document.getElementById('result').innerHTML='';
    try{
      const seconds=Math.min(Number(document.getElementById('duration').value)||5,video.duration||5),frames=Math.max(1,Math.round(seconds*FPS));
      const support=await VideoEncoder.isConfigSupported({codec:'vp8',width:W,height:H,framerate:FPS,bitrate:8_000_000});
      if(!support.supported)throw new Error('Máy này không hỗ trợ VP8 WebCodecs 1080×1920.');
      const muxer=new WebMMuxer(W,H,FPS,seconds*1000);
      const encoder=new VideoEncoder({output:chunk=>muxer.add(chunk),error:error=>{throw error;}});
      encoder.configure(support.config);
      for(let i=0;i<frames;i++){
        const t=i/FPS;await seek(t%(video.duration||seconds));drawFrame(i/frames);
        const frame=new VideoFrame(canvas,{timestamp:Math.round(t*1e6),duration:Math.round(1e6/FPS)});
        encoder.encode(frame,{keyFrame:i%FPS===0});frame.close();
        if(i%5===0){status.textContent=`Đang encode ${i+1}/${frames} frame (${Math.round((i+1)/frames*100)}%)…`;await new Promise(r=>setTimeout(r));}
      }
      await encoder.flush();encoder.close();const blob=muxer.finish(),url=URL.createObjectURL(blob);
      document.getElementById('result').innerHTML=`<a class="download" href="${url}" download="canvas-webcodecs-test.webm">Tải WebM ${(blob.size/1048576).toFixed(1)} MB</a>`;
      status.textContent=`✓ Xong ${frames} frame, 1080×1920 @ ${FPS}fps. Không quay màn hình.`;
    }catch(error){status.textContent='✗ '+(error.message||String(error));}
    finally{button.disabled=false;}
  };
  drawFrame(0);
})();
