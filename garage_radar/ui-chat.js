'use strict';
const chatDialog=document.getElementById('assistantChat');
const chatMessages=document.getElementById('chatMessages');
document.getElementById('openChat').onclick=()=>{chatDialog.showModal();document.getElementById('chatInput').focus()};
document.getElementById('closeChat').onclick=()=>chatDialog.close();
function chatBubble(text,kind){const p=document.createElement('p');p.className='chat-bubble '+kind;p.textContent=text;chatMessages.append(p);chatMessages.scrollTop=chatMessages.scrollHeight;return p}
document.getElementById('chatForm').onsubmit=async event=>{
  event.preventDefault();const input=document.getElementById('chatInput'),send=document.getElementById('sendChat'),status=document.getElementById('chatStatus');
  const text=input.value.trim();if(!text||send.disabled)return;
  chatBubble(text,'user');input.value='';send.disabled=true;status.textContent='ИИ разбирает запрос и проверяет объявления…';
  try{
    const response=await api('/api/chat',{text,selected_id:selected});
    chatBubble(response.answer,'assistant');
    if(response.search_url){const link=document.createElement('a');link.href=response.search_url;link.textContent='Открыть поиск на Авито ↗';link.target='_blank';link.rel='noopener noreferrer';link.className='chat-source-link';chatMessages.append(link)}
    for(const item of response.items||[]){
      const card=document.createElement('button');card.className='chat-result';
      card.innerHTML=`${item.photo_url?`<img loading="lazy" src="${esc(item.photo_url)}" alt="Фото из объявления">`:''}<span><strong>${esc(money(item.price_rub))}</strong><span>${esc(item.title)}</span>${cityBadge(item)}${comparisonBadge(item)}</span>`;
      card.onclick=async()=>{chatDialog.close();if(category!==item.category){selectCategory(item.category);await loadList()}openItem(item.id)};chatMessages.append(card);
    }
    chatMessages.scrollTop=chatMessages.scrollHeight;status.textContent='Можно уточнить условия, написать «ещё» или «разбери первый».';
  }catch(error){chatBubble(error.message,'error');input.value=text;status.textContent='Сообщение не завершено. Можно повторить запрос.'}
  finally{send.disabled=false;input.focus()}
};
