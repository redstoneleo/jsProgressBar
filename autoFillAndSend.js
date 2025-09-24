// ==UserScript==
// @name         自动填充并发送到 Gemini & Grok & ChatGPT (四层优先+按钮检测版)
// @namespace    http://tampermonkey.net/
// @version      3.1
// @description  当前光标输入框 > 最近活跃输入框 > 通用选择器 > 站点专用选择器，并用按钮可用检测发送（按钮优先，回车兜底）
// @match        https://chatgpt.com/*
// @match        https://gemini.google.com/*
// @match        https://copilot.microsoft.com/*
// @match        https://grok.com/*
// @match        https://poe.com/*
// @match        https://x.com/i/grok*
// @match        https://bot.n.cn/chathome*
// @match        https://www.deepl.com/translator*
// @match        https://fanyi.baidu.com/*
// @match        https://dict.eudic.net/liju/en/*
// @match        https://dictionary.cambridge.org/*
// @match        https://www.collinsdictionary.com/*
// @match        https://www.ldoceonline.com/*
// @match        https://www.oxfordlearnersdictionaries.com/*
// @grant        none
// @run-at       document-idle
// ==/UserScript==

(function() {
    'use strict';
let lastActive = null;
document.addEventListener('focusin', e => {
    if (e.target.matches('input:not([disabled]):not([readonly]), textarea:not([disabled]):not([readonly]), [contenteditable="true"]')) {
        lastActive = e.target;
    }
});

function selectAllAndDelete(el) {
    if (!el || !el.isContentEditable) return;

    const sel = window.getSelection();
    const range = document.createRange();

    // 选中 el 内所有内容
    range.selectNodeContents(el);
    sel.removeAllRanges();
    sel.addRange(range);

    // 删除选区内容（DOM 操作）
    range.deleteContents();

    // 触发编辑器事件，让它感知删除
    el.dispatchEvent(new InputEvent('beforeinput', {
        bubbles: true,
        inputType: 'deleteContent',
        data: null
    }));
    el.dispatchEvent(new InputEvent('input', {
        bubbles: true,
        inputType: 'deleteContent',
        data: null
    }));
}

function setNativeValue(element, value) {
    const valueSetter = Object.getOwnPropertyDescriptor(element.__proto__, 'value')?.set
        || Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value')?.set;
    if (valueSetter) {
        valueSetter.call(element, value);
    } else {
        element.value = value;
    }
}


function fillInputUniversal(el, text) {
    if (!el) return;
    el.focus();

    if (el.isContentEditable) {
        // 选项 C: 清空并模拟完整 IME 输入序列（针对 Slate + 中文）
        // 先清空
        // selectAllAndDelete(el);
      document.execCommand('selectAll');
      document.execCommand('delete');

        // 模拟 composition 事件（中文输入）
        const events = [
            new CompositionEvent('compositionstart', { bubbles: true }),
            new CompositionEvent('compositionupdate', { bubbles: true, data: text }),
            new CompositionEvent('compositionend', { bubbles: true, data: text }),
            new InputEvent('beforeinput', { bubbles: true, inputType: 'insertCompositionText', data: text }),
            new InputEvent('input', { bubbles: true, inputType: 'insertCompositionText', data: text }),
            new Event('change', { bubbles: true })
        ];
        events.forEach(event => el.dispatchEvent(event));

        // 加 keyup 以模拟输入结束
        el.dispatchEvent(new KeyboardEvent('keyup', { bubbles: true, key: 'Process', code: 'Process' }));

        console.log('✅ Slate 输入模拟完成，文本：', text, '当前内容：', el.innerText);  // 调试用
    } else {
        // 原生 input/textarea
        setNativeValue(el, text);
        el.dispatchEvent(new InputEvent('input', { bubbles: true }));
        el.dispatchEvent(new Event('change', { bubbles: true }));
    }
}

function pressKey(inputBox, keyName) {
    // 如果 keyName 是单字符，比如 "a"，就自动补充 code 和 keyCode
    let code = keyName.length === 1 ? `Key${keyName.toUpperCase()}` : keyName;
    let keyCode = keyName.length === 1 ? keyName.toUpperCase().charCodeAt(0) : 0;

    const eventOptions = {
        bubbles: true,
        cancelable: true,
        key: keyName,
        code: code,
        keyCode: keyCode,
        which: keyCode
    };

    ['keydown', 'keypress', 'keyup'].forEach(type => {
        const event = new KeyboardEvent(type, eventOptions);
        inputBox.dispatchEvent(event);
    });
}


function waitForButtonAndSend(inputBox, sendButtonSelector, timeout = 1000) {
    const start = Date.now();
    const timer = setInterval(() => {
        let btn = sendButtonSelector ? document.querySelector(sendButtonSelector) : null;
        if (!btn) {
            btn = document.querySelector(
                'button[type="submit"]:not([disabled]), button[aria-label*="Send"]:not([disabled]), ' +
                'button[data-testid*="send"]:not([disabled]), button[title*="Send"]:not([disabled])'
            );
        }
        if (btn && !btn.disabled && btn.offsetParent !== null) {
            clearInterval(timer);
            btn.click();
        } else if (Date.now() - start > timeout) {
            clearInterval(timer);
            pressKey(inputBox, 'Enter'); // 超时兜底
        }
    }, 50);
}


function autoFillAndSend(textToSend, selectorInfo) {
    const findAndFill = () => {
        let inputBox = null;
        let sendButtonSelector = null;
        let usedSelectorType = null;

        // 1️⃣ 优先用 selectorInfo
        if (selectorInfo && selectorInfo.inputBoxSelector) {
            inputBox = document.querySelector(selectorInfo.inputBoxSelector);
            sendButtonSelector = selectorInfo.sendButtonSelector || null;
            if (inputBox) usedSelectorType = "selectorInfo";
        }

        // 2️⃣ 当前光标所在输入框
        if (!inputBox && document.activeElement &&
            document.activeElement.matches('input:not([disabled]):not([readonly]), textarea:not([disabled]):not([readonly]), [contenteditable="true"]')
        ) {
            inputBox = document.activeElement;
            usedSelectorType = "当前光标所在输入框";
        }

        // 3️⃣ 最近活跃输入框
        if (!inputBox && lastActive) {
            inputBox = lastActive;
            usedSelectorType = "最近活跃输入框";
        }

        // 4️⃣ 通用选择器
        if (!inputBox) {
            inputBox = document.querySelector(
                'input[type="text"]:not([disabled]):not([readonly]), ' +
                'input[type="search"]:not([disabled]):not([readonly]), ' +
                'textarea:not([disabled]):not([readonly]), ' +
                '[contenteditable="true"]'
            );
            if (inputBox) usedSelectorType = "通用选择器";
        }

        if (inputBox) {
            clearInterval(checkInterval);
            console.log(`✅ 当前站点使用了：${usedSelectorType}`);
            inputBox.focus();
            fillInputUniversal(inputBox, textToSend);
            waitForButtonAndSend(inputBox, sendButtonSelector);
        }
    };

    const checkInterval = setInterval(findAndFill, 500);
}


    // === 修改这里的文本 ===
    autoFillAndSend("励志电视剧，2000年以前");

})();
