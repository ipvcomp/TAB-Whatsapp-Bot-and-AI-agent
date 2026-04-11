import React, { useState } from 'react';
import { useChat } from '../context/ChatContext';

export default function ListMenu({ items, footer, listType }) {
  const { handleButtonClick } = useChat();
  const [selected, setSelected] = useState(null);
  const isBankList = listType === 'banks';

  const handleSelect = (id) => {
    if (isBankList) setSelected(id);
    handleButtonClick(id);
  };

  // Split footer into page-nav buttons and action buttons
  const pageNavBtns = (footer || []).filter((b) => b.id.startsWith('payout_bank_page_'));
  const actionBtns  = (footer || []).filter((b) => !b.id.startsWith('payout_bank_page_'));

  return (
    <div className="mt-2 mb-1 animate-fade-in">
      <div className="bg-white rounded-xl shadow-sm overflow-hidden border border-gray-100">
        {items.map((item, idx) => {
          const isSelected = selected === item.id;
          return (
            <button
              key={item.id}
              onClick={() => handleSelect(item.id)}
              className={`
                w-full text-left px-4 py-3 text-sm
                transition-colors duration-100
                flex items-center gap-3
                ${idx < items.length - 1 ? 'border-b border-gray-100' : ''}
                ${isSelected ? 'bg-teal-50' : 'hover:bg-gray-50 active:bg-gray-100'}
              `}
            >
              {isBankList && (
                <span className="text-base flex-shrink-0">🏦</span>
              )}
              <div className="flex-1 min-w-0">
                <div className={`font-medium truncate ${isSelected ? 'text-teal-700' : 'text-gray-800'}`}>
                  {item.label}
                </div>
                {item.subtitle && (
                  <div className="text-xs text-gray-400 mt-0.5">{item.subtitle}</div>
                )}
              </div>
              {isBankList ? (
                /* Radio circle */
                <div className={`
                  w-5 h-5 rounded-full border-2 flex items-center justify-center flex-shrink-0
                  ${isSelected
                    ? 'border-teal-500 bg-teal-500'
                    : 'border-gray-300 bg-white'}
                `}>
                  {isSelected && (
                    <svg className="w-3 h-3 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={3} d="M5 13l4 4L19 7" />
                    </svg>
                  )}
                </div>
              ) : (
                /* Default chevron */
                <svg
                  className="w-4 h-4 text-gray-400 flex-shrink-0"
                  fill="none" viewBox="0 0 24 24" stroke="currentColor"
                >
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
                </svg>
              )}
            </button>
          );
        })}
      </div>

      {/* Page navigation buttons (Prev / Next) */}
      {pageNavBtns.length > 0 && (
        <div className="flex gap-2 mt-2">
          {pageNavBtns.map((btn) => {
            const isPrev = btn.label.startsWith('◀');
            return (
              <button
                key={btn.id}
                onClick={() => handleButtonClick(btn.id)}
                className={`
                  flex-1 text-xs font-semibold px-3 py-2 rounded-xl
                  active:scale-95 transition-all shadow-sm whitespace-nowrap
                  ${isPrev
                    ? 'bg-white text-gray-700 border border-gray-300 hover:bg-gray-50'
                    : 'bg-teal-500 text-white hover:bg-teal-600'}
                `}
              >
                {btn.label}
              </button>
            );
          })}
        </div>
      )}

      {/* Action buttons (search again, etc.) */}
      {actionBtns.length > 0 && (
        <div className="mt-2 flex flex-col gap-1.5">
          {actionBtns.map((btn) => (
            <button
              key={btn.id}
              onClick={() => handleButtonClick(btn.id)}
              className="
                w-full text-xs font-medium px-4 py-2.5 rounded-xl
                bg-amber-50 text-amber-700 border border-amber-200
                hover:bg-amber-100 active:scale-[0.98] transition-all shadow-sm
              "
            >
              {btn.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
