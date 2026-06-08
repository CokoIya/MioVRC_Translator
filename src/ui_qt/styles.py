from __future__ import annotations

from src.ui_qt.icon_utils import ui_icon_url
from src.ui_qt.theme import theme_tokens


_FONT_STACK = '"Segoe UI Variable Text", "Microsoft YaHei UI", "Segoe UI", sans-serif'


def _base(theme: object) -> str:
    c = theme_tokens(theme)
    combo_arrow = ui_icon_url("chevron-down-muted.svg")
    is_dark = str(c["APP_BG"]).lower() == "#07080d"
    field_bg = "rgba(9, 13, 20, 0.72)" if is_dark else "rgba(248, 251, 255, 0.70)"
    field_hover = "rgba(22, 28, 42, 0.72)" if is_dark else "rgba(238, 245, 250, 0.78)"
    button_bg = "rgba(255, 255, 255, 0.04)" if is_dark else "rgba(255, 255, 255, 0.56)"
    return f"""
    QWidget {{
        color: {c["TEXT_PRIMARY"]};
        font-family: {_FONT_STACK};
        font-size: 14px;
    }}
    QDialog, QMainWindow {{
        background: {c["APP_BG"]};
    }}
    QToolTip {{
        background: {c["PANEL_BG"]};
        color: {c["TEXT_PRIMARY"]};
        border: 1px solid {c["PANEL_BORDER"]};
        padding: 6px 8px;
        border-radius: 8px;
    }}
    QScrollBar:vertical {{
        background: transparent;
        width: 10px;
        margin: 4px 2px 4px 0;
    }}
    QScrollBar::handle:vertical {{
        background: {c["PANEL_BORDER"]};
        min-height: 26px;
        border-radius: 5px;
    }}
    QScrollBar::handle:vertical:hover {{
        background: {c["TEXT_MUTED"]};
    }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical,
    QScrollBar:horizontal, QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal,
    QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{
        background: transparent;
        border: 0;
        width: 0px;
        height: 0px;
    }}
    QPlainTextEdit, QTextEdit, QLineEdit, QComboBox {{
        background: {field_bg};
        border: 1px solid {c["FIELD_BORDER"]};
        border-radius: {c["RADIUS_M"]}px;
        color: {c["INPUT_TEXT"]};
        selection-background-color: {c["ACCENT"]};
    }}
    QPlainTextEdit:hover, QTextEdit:hover, QLineEdit:hover, QComboBox:hover {{
        background: {field_hover};
    }}
    QPlainTextEdit:focus, QTextEdit:focus, QLineEdit:focus, QComboBox:focus {{
        border-color: {c["FIELD_FOCUS"]};
    }}
    QLineEdit {{
        min-height: 18px;
        padding: 10px 12px;
    }}
    QPlainTextEdit, QTextEdit {{
        padding: 12px;
    }}
    QComboBox {{
        min-height: 18px;
        padding: 7px 34px 7px 12px;
    }}
    QComboBox::drop-down {{
        border: 0;
        width: 26px;
    }}
    QComboBox::down-arrow {{
        image: {combo_arrow};
        width: 13px;
        height: 13px;
        margin-right: 9px;
    }}
    QComboBox QAbstractItemView {{
        background: {c["PANEL_BG"]};
        color: {c["TEXT_PRIMARY"]};
        border: 1px solid {c["PANEL_BORDER"]};
        border-radius: 12px;
        padding: 6px;
        selection-background-color: {c["ACCENT_SOFT"]};
        selection-color: {c["TEXT_PRIMARY"]};
        outline: 0;
    }}
    QPushButton {{
        background: {button_bg};
        color: {c["TEXT_PRIMARY"]};
        border: 1px solid {c["FIELD_BORDER"]};
        border-radius: {c["RADIUS_M"]}px;
        padding: 8px 13px;
        min-height: 18px;
        font-size: 14px;
        font-weight: 600;
    }}
    QPushButton:hover {{
        background: {c["FIELD_HOVER"]};
        border-color: {c["PANEL_BORDER"]};
    }}
    QPushButton:pressed {{
        background: {c["PANEL_RAISED"]};
    }}
    QPushButton:disabled {{
        color: {c["TEXT_MUTED"]};
        background: {c["PANEL_ALT_BG"]};
        border-color: {c["PANEL_DIVIDER"]};
    }}
    QPushButton#primaryButton {{
        background: {c["ACCENT"]};
        color: #ffffff;
        border: 1px solid {c["ACCENT"]};
        font-weight: 700;
    }}
    QPushButton#primaryButton:hover {{
        background: {c["ACCENT_HOVER"]};
        border-color: {c["ACCENT_HOVER"]};
    }}
    QPushButton#secondaryButton, QPushButton#headerButton, QPushButton#ghostButton, QPushButton#linkButton {{
        background: {button_bg};
    }}
    QPushButton#dangerButton {{
        background: {c["DANGER"]};
        color: {c["TEXT_INVERTED"]};
        border: 1px solid {c["DANGER"]};
        font-weight: 700;
    }}
    QPushButton#dangerButton:hover {{
        background: #d73737;
        border-color: #d73737;
    }}
    QPushButton#iconButton {{
        min-width: {c["CONTROL_H"]}px;
        min-height: {c["CONTROL_H"]}px;
        max-width: {c["CONTROL_H"]}px;
        max-height: {c["CONTROL_H"]}px;
        padding: 0;
        border-radius: {int(c['CONTROL_H']) // 2}px;
    }}
    QPushButton#themeIconButton, QPushButton#swapIconButton {{
        background: transparent;
        border: 0;
        padding: 0;
        border-radius: 999px;
    }}
    QPushButton#themeIconButton:hover, QPushButton#swapIconButton:hover {{
        background: {c["FIELD_HOVER"]};
        border: 0;
    }}
    QPushButton#themeIconButton:pressed, QPushButton#swapIconButton:pressed {{
        background: {c["PANEL_RAISED"]};
    }}
    QPushButton#headerButton {{
        min-height: {c["CONTROL_H"]}px;
        padding: 0 16px;
        font-weight: 600;
    }}
    QPushButton#sponsorButton {{
        background: #c99a2d;
        border-color: #c99a2d;
        color: white;
        min-height: {c["CONTROL_H"]}px;
        padding: 0 16px;
        font-weight: 700;
    }}
    QLabel#sectionTitle {{
        color: {c["TEXT_PRIMARY"]};
        font-size: 18px;
        font-weight: 750;
    }}
    QLabel#sectionHint, QLabel#mutedLabel, QLabel#bottomLabel, QLabel#fieldHint, QLabel#hintLabel {{
        color: {c["TEXT_SECONDARY"]};
        font-size: 13px;
    }}
    QLabel#fieldHint {{
        padding-left: 182px;
        padding-bottom: 4px;
    }}
    QLabel#successLabel {{
        color: {c["SUCCESS"]};
        font-size: 13px;
        font-weight: 600;
    }}
    QLabel#errorLabel {{
        color: {c["DANGER"]};
        font-size: 13px;
        font-weight: 600;
    }}
    QLabel#fieldLabel, QLabel#controlLabel {{
        color: {c["TEXT_SECONDARY"]};
        font-size: 13px;
        font-weight: 700;
    }}
    QLabel#recommendationPill {{
        background: {c["WARNING_SOFT"]};
        border: 1px solid {c["WARNING_BORDER"]};
        border-radius: {c["RADIUS_M"]}px;
        color: {c["WARNING"]};
        padding: 5px 10px;
        font-size: 12px;
        font-weight: 800;
    }}
    QLabel#eyebrowLabel {{
        color: {c["TEXT_MUTED"]};
        font-size: 12px;
        font-weight: 700;
        text-transform: uppercase;
    }}
    QLabel#statusPill {{
        color: {c["SUCCESS"]};
        background: transparent;
        border: 0;
        border-radius: 0;
        padding: 0 4px;
        font-size: 12px;
        font-weight: 800;
    }}
    QLabel#versionPill {{
        background: {c["PANEL_ALT_BG"]};
        border: 1px solid {c["PANEL_BORDER"]};
        color: {c["TEXT_SECONDARY"]};
        border-radius: 14px;
        padding: 4px 10px;
        font-size: 12px;
        font-weight: 700;
    }}
    QProgressBar {{
        background: {c["PANEL_ALT_BG"]};
        border: 1px solid {c["PANEL_DIVIDER"]};
        border-radius: 999px;
    }}
    QProgressBar::chunk {{
        background: {c["ACCENT"]};
        border-radius: 999px;
    }}
    """


def build_app_stylesheet(theme: object) -> str:
    return _base(theme)



def build_main_window_styles(theme: object) -> str:
    c = theme_tokens(theme)
    is_dark = str(c["APP_BG"]).lower() == "#07080d"
    shell_bg = "rgba(7, 9, 14, 0.18)" if is_dark else "rgba(247, 250, 255, 0.20)"
    glass_bg = "rgba(14, 18, 28, 0.68)" if is_dark else "rgba(247, 250, 255, 0.66)"
    glass_alt_bg = "rgba(22, 27, 40, 0.62)" if is_dark else "rgba(239, 246, 252, 0.58)"
    glass_raised_bg = "rgba(28, 34, 50, 0.68)" if is_dark else "rgba(232, 241, 249, 0.62)"
    glass_border = "rgba(120, 154, 200, 0.32)" if is_dark else "rgba(93, 115, 145, 0.26)"
    strong_border = "rgba(47, 111, 255, 0.62)" if is_dark else "rgba(0, 152, 199, 0.52)"
    return _base(theme) + f"""
    #shellPanel {{
        background: transparent;
        border: 0;
    }}
    #appChrome {{
        background: {shell_bg};
        border: 0;
        border-radius: 0px;
    }}
    #headerPanel {{
        background: {glass_bg};
        border: 1px solid {glass_border};
        border-radius: {c["RADIUS_L"]}px;
    }}
    #workspacePanel {{
        background: transparent;
        border: 0;
    }}
    #translationCard {{
        background: {glass_bg};
        border: 1px solid {glass_border};
        border-radius: {c["RADIUS_L"]}px;
    }}
    #translationPanel, #editorPanel, #actionStrip, #modeBox, #sidePanel, #controlGroup, #statusModule, #langFlowPanel {{
        background: {glass_alt_bg};
        border: 1px solid {glass_border};
        border-radius: {c["RADIUS_L"]}px;
    }}
    #actionStrip {{
        background: {glass_raised_bg};
    }}
    #langFlowPanel {{
        background: {glass_raised_bg};
    }}
    #sidePanel {{
        background: {glass_bg};
        border: 1px solid {glass_border};
        border-radius: {c["RADIUS_L"]}px;
        padding: 16px;
    }}
    #controlGroup {{
        background: {glass_alt_bg};
        border: 1px solid {glass_border};
        border-radius: {c["RADIUS_M"]}px;
    }}
    #editorPanel[role="source"] {{
        background: {glass_alt_bg};
        border-color: {glass_border};
    }}
    #editorPanel[role="target"] {{
        background: {glass_raised_bg};
        border-color: {strong_border};
    }}
    QLabel#brandTitle {{
        color: {c["HEADER_TEXT"]};
        font-size: 25px;
        font-weight: 800;
    }}
    QLabel#brandSubtitle {{
        color: {c["HEADER_MUTED"]};
        font-size: 13px;
    }}
    QLabel#sectionTitleMain {{
        color: {c["TEXT_PRIMARY"]};
        font-size: 15px;
        font-weight: 800;
    }}
    QLabel#counterLabel {{
        color: {c["TEXT_MUTED"]};
        font-size: 12px;
        font-weight: 700;
    }}
    QLabel#controlSectionTitle {{
        color: {c["TEXT_PRIMARY"]};
        font-size: 16px;
        font-weight: 800;
    }}
    QLabel#hudEyebrow {{
        color: {c["ACCENT"]};
        font-size: 11px;
        font-weight: 800;
    }}
    QLabel#miniMetric {{
        color: {c["TEXT_SECONDARY"]};
        font-size: 12px;
        font-weight: 700;
    }}
    QFrame#lineDivider {{
        background: {c["PANEL_DIVIDER"]};
        min-width: 1px;
        max-width: 1px;
        border: 0;
    }}
    QFrame#footerPanel {{
        background: {glass_bg};
        border: 1px solid {glass_border};
        border-radius: {c["RADIUS_L"]}px;
    }}
    QPushButton#activeButton {{
        background: rgba(255, 255, 255, 0.03);
        border: 1px solid {glass_border};
        color: {c["TEXT_PRIMARY"]};
        text-align: center;
    }}
    QPushButton#activeButton[active="true"] {{
        background: {c["ACCENT_SOFT"]};
        border: 1px solid {c["ACCENT"]};
        color: {c["TEXT_PRIMARY"]};
    }}
    QPushButton#modeButton {{
        border: 1px solid transparent;
        font-weight: 700;
        color: {c["TEXT_SECONDARY"]};
    }}
    QPushButton#modeButton[modeActive="true"], QPushButton#modeButton:checked {{
        background: {c["ACCENT"]};
        color: #ffffff;
        border: 1px solid {c["ACCENT"]};
        font-weight: 800;
    }}
    QPushButton#modeButton[modeActive="true"]:hover, QPushButton#modeButton:checked:hover {{
        background: {c["ACCENT_HOVER"]};
        border-color: {c["ACCENT_HOVER"]};
    }}
    QPushButton#modeButton[modeActive="false"] {{
        background: transparent;
        border-color: transparent;
    }}
    QPushButton#modeButton[modeActive="false"]:hover {{
        background: {c["FIELD_HOVER"]};
        border-color: {c["FIELD_BORDER"]};
        color: {c["TEXT_PRIMARY"]};
    }}
    QPlainTextEdit#textPane {{
        background: transparent;
        border: 0;
        color: {c["EDITOR_TEXT"]};
        font-size: 16px;
        padding: 4px 2px;
    }}
    QPlainTextEdit#textPane:focus {{
        border: 0;
    }}
    QComboBox#headerCombo {{
        min-height: 26px;
        max-height: 26px;
        min-width: 132px;
        padding: 6px 34px 6px 12px;
    }}
    QComboBox#langCombo {{
        min-height: 30px;
        max-height: 32px;
        padding: 5px 30px 5px 10px;
    }}
    QPushButton#railButton {{
        min-height: {c["CONTROL_H"]}px;
    }}
    QPushButton#updateBadge {{
        background: {c["WARNING_SOFT"]};
        border: 1px solid {c["WARNING_BORDER"]};
        color: {c["WARNING"]};
        border-radius: 14px;
        min-height: 30px;
        padding: 4px 12px;
    }}
    QPushButton#socialButton {{
        min-width: {c["CONTROL_H"]}px;
        min-height: {c["CONTROL_H"]}px;
        max-width: {c["CONTROL_H"]}px;
        max-height: {c["CONTROL_H"]}px;
        padding: 0;
        border-radius: {int(c['CONTROL_H']) // 2}px;
        background: rgba(255, 255, 255, 0.04);
    }}
    QPushButton#deviceButton, QComboBox#deviceCombo {{
        background: rgba(255, 255, 255, 0.04);
        border: 1px solid {glass_border};
        color: {c["TEXT_PRIMARY"]};
        text-align: left;
        padding: 6px 34px 6px 12px;
        min-height: 24px;
        max-height: 24px;
    }}
    QPushButton#deviceButton:hover, QComboBox#deviceCombo:hover {{
        background: {c["FIELD_HOVER"]};
        border-color: {c["ACCENT_BORDER"]};
    }}
    """



def build_settings_window_styles(theme: object) -> str:
    c = theme_tokens(theme)
    is_dark = str(c["APP_BG"]).lower() == "#07080d"
    shell_bg = "rgba(7, 9, 14, 0.18)" if is_dark else "rgba(247, 250, 255, 0.20)"
    glass_bg = "rgba(14, 18, 28, 0.68)" if is_dark else "rgba(247, 250, 255, 0.66)"
    glass_alt_bg = "rgba(22, 27, 40, 0.62)" if is_dark else "rgba(239, 246, 252, 0.58)"
    glass_raised_bg = "rgba(28, 34, 50, 0.68)" if is_dark else "rgba(232, 241, 249, 0.62)"
    glass_border = "rgba(120, 154, 200, 0.32)" if is_dark else "rgba(93, 115, 145, 0.26)"
    return _base(theme) + f"""
    QDialog {{
        background: {c["APP_BG"]};
    }}
    QWidget#settingsBackground {{
        background: transparent;
        border: 0;
    }}
    #settingsChrome {{
        background: {shell_bg};
        border: 0;
        border-radius: 0px;
    }}
    #headerCard {{
        background: {glass_bg};
        border: 1px solid {glass_border};
        border-radius: {c["RADIUS_L"]}px;
    }}
    #headerTitle {{
        color: {c["HEADER_TEXT"]};
        font-size: 25px;
        font-weight: 800;
    }}
    #headerSubtitle {{
        color: {c["HEADER_MUTED"]};
        font-size: 13px;
    }}
    #versionLabel {{
        color: {c["HEADER_MUTED"]};
        font-size: 13px;
        font-weight: 700;
    }}
    #navPanel {{
        background: {glass_bg};
        border: 1px solid {glass_border};
        border-radius: {c["RADIUS_L"]}px;
    }}
    #navTitle {{
        color: {c["TEXT_PRIMARY"]};
        font-size: 19px;
        font-weight: 800;
        padding: 0 12px;
    }}
    QListWidget#navList {{
        background: transparent;
        border: 0;
        outline: 0;
        padding: 10px 12px 12px 12px;
    }}
    QListWidget#navList::item {{
        color: {c["TEXT_SECONDARY"]};
        background: transparent;
        border: 1px solid transparent;
        border-radius: {c["RADIUS_M"]}px;
        padding-left: 12px;
        margin-bottom: 6px;
        font-size: 14px;
        font-weight: 600;
    }}
    QListWidget#navList::item:hover {{
        background: {c["FIELD_HOVER"]};
        color: {c["TEXT_PRIMARY"]};
    }}
    QListWidget#navList::item:selected {{
        background: {c["ACCENT_SOFT"]};
        border-color: {c["ACCENT_BORDER"]};
        color: {c["TEXT_PRIMARY"]};
    }}
    #pageStack {{
        background: transparent;
        border: 0;
    }}
    QScrollArea#settingsPageScroll,
    QScrollArea#settingsPageScroll > QWidget,
    QScrollArea#settingsPageScroll QWidget#qt_scrollarea_viewport,
    QFrame#settingsPageContent,
    QStackedWidget#pageStack {{
        background: transparent;
        border: 0;
    }}
    QFrame#settingsPageSurface {{
        background: {glass_bg};
        border: 1px solid {glass_border};
        border-radius: {c["RADIUS_L"]}px;
    }}
    QFrame#settingsSectionCard, QFrame#subCard, QGroupBox {{
        background: {glass_alt_bg};
        border: 1px solid {glass_border};
        border-radius: {c["RADIUS_L"]}px;
    }}
    QLabel#pageTitle {{
        color: {c["TEXT_PRIMARY"]};
        font-size: 24px;
        font-weight: 770;
    }}
    QLabel#pageHint {{
        color: {c["TEXT_SECONDARY"]};
        font-size: 13px;
    }}
    QLabel#sectionTitle {{
        color: {c["TEXT_PRIMARY"]};
        font-size: 17px;
        font-weight: 750;
    }}
    QLabel#fieldLabel {{
        color: {c["TEXT_SECONDARY"]};
        font-size: 13px;
        font-weight: 700;
    }}
    #settingsFooter {{
        background: {glass_bg};
        border: 0;
        border-top: 1px solid {glass_border};
        border-radius: 0px;
    }}
    QFrame#modelInfoCard {{
        background: rgba(255, 255, 255, 0.035);
        border: 1px solid {glass_border};
        border-radius: {c["RADIUS_M"]}px;
    }}
    QLabel#modelInfoTitle {{
        color: {c["TEXT_PRIMARY"]};
        font-size: 13px;
        font-weight: 800;
    }}
    QLabel#modelBadge {{
        background: {c["PANEL_ALT_BG"]};
        border: 1px solid {glass_border};
        border-radius: 9px;
        color: {c["TEXT_SECONDARY"]};
        padding: 4px 8px;
        font-size: 12px;
        font-weight: 800;
    }}
    QLabel#modelBadge[tone="very_recommended"], QLabel#modelBadge[tone="recommended"] {{
        background: {c["SUCCESS_SOFT"]};
        border-color: {c["SUCCESS_BORDER"]};
        color: {c["SUCCESS"]};
    }}
    QLabel#modelBadge[tone="not_recommended"] {{
        background: {c["DANGER_SOFT"]};
        border-color: {c["DANGER_BORDER"]};
        color: {c["DANGER"]};
    }}
    QFrame#runtimeNotice {{
        background: rgba(255, 255, 255, 0.03);
        border: 1px solid {glass_border};
        border-radius: {c["RADIUS_M"]}px;
    }}
    QFrame#sbv2OptionsFrame {{
        background: transparent;
        border: 0;
    }}
    QFrame#voiceDropZone {{
        background: rgba(255, 255, 255, 0.025);
        border: 1px dashed {glass_border};
        border-radius: {c["RADIUS_M"]}px;
    }}
    QPushButton#primaryButton {{
        min-height: 34px;
    }}
    QPushButton#secondaryButton {{
        background: rgba(255, 255, 255, 0.04);
        border: 1px solid {glass_border};
        min-height: 34px;
    }}
    QPushButton#footerPrimaryButton {{
        background: {c["ACCENT"]};
        color: #ffffff;
        border: 1px solid {c["ACCENT"]};
        border-radius: {c["RADIUS_M"]}px;
        min-height: 34px;
        max-height: 34px;
        padding: 0 14px;
        font-weight: 700;
    }}
    QPushButton#footerPrimaryButton:hover {{
        background: {c["ACCENT_HOVER"]};
        border-color: {c["ACCENT_HOVER"]};
    }}
    QPushButton#footerSecondaryButton {{
        background: rgba(255, 255, 255, 0.04);
        color: {c["TEXT_PRIMARY"]};
        border: 1px solid {glass_border};
        border-radius: {c["RADIUS_M"]}px;
        min-height: 34px;
        max-height: 34px;
        padding: 0 14px;
        font-weight: 600;
    }}
    QPushButton#footerSecondaryButton:hover {{
        background: {c["FIELD_HOVER"]};
        border-color: {c["PANEL_BORDER"]};
    }}
    """



def build_text_input_styles(theme: object) -> str:
    c = theme_tokens(theme)
    return _base(theme) + f"""
    QDialog {{
        background: transparent;
    }}
    QFrame#textInputShell {{
        background: {c["SHELL_BG"]};
        border: 1px solid {c["SHELL_BORDER"]};
        border-radius: {c["RADIUS_L"]}px;
    }}
    QLabel#opacityLabel, QLabel#textInputCounter {{
        color: {c["TEXT_SECONDARY"]};
        font-size: 11px;
    }}
    QTextEdit#inputTextEdit {{
        background: {c["PANEL_BG"]};
        border: 1px solid {c["FIELD_BORDER"]};
        border-radius: {c["RADIUS_M"]}px;
        color: {c["TEXT_PRIMARY"]};
        padding: 12px;
        font-size: 15px;
    }}
    QTextEdit#inputTextEdit:focus {{
        border-color: {c["ACCENT"]};
    }}
    QPushButton#pinButton {{
        min-width: 30px;
        max-width: 30px;
        min-height: 30px;
        max-height: 30px;
        padding: 0;
        border-radius: 10px;
    }}
    QPushButton#iconButton {{
        min-width: 30px;
        max-width: 30px;
        min-height: 30px;
        max-height: 30px;
        padding: 0;
        border-radius: 10px;
    }}
    QPushButton#primaryButton {{
        min-height: 34px;
        padding: 0 14px;
    }}
    QSlider::groove:horizontal {{
        background: {c["PANEL_BORDER"]};
        border-radius: 4px;
        height: 7px;
    }}
    QSlider::handle:horizontal {{
        background: {c["ACCENT"]};
        border-radius: 7px;
        width: 14px;
        margin: -4px 0;
    }}
    """



def build_floating_window_styles(theme: object) -> str:
    c = theme_tokens(theme)
    return _base(theme) + f"""
    QDialog {{
        background: {c["SHELL_BG"]};
        border: 1px solid {c["SHELL_BORDER"]};
        border-radius: {c["RADIUS_L"]}px;
    }}
    QScrollArea, QWidget#floatingScrollContent {{
        background: {c["PANEL_BG"]};
        border: 1px solid {c["PANEL_BORDER"]};
        border-radius: {c["RADIUS_L"]}px;
    }}
    QLabel#opacityLabel {{
        color: {c["TEXT_SECONDARY"]};
        font-size: 11px;
    }}
    QLabel#floatingStatusLabel {{
        color: {c["TEXT_SECONDARY"]};
        background: {c["FIELD_BG"]};
        border: 1px solid {c["FIELD_BORDER"]};
        border-radius: {c["RADIUS_M"]}px;
        min-height: 28px;
        padding: 0 10px;
        font-size: 11px;
        font-weight: 700;
    }}
    QPushButton#pinButton {{
        min-width: 30px;
        max-width: 30px;
        min-height: 30px;
        max-height: 30px;
        padding: 0;
        border-radius: 10px;
    }}
    QPushButton#primaryButton {{
        min-height: 34px;
        padding: 0 14px;
    }}
    QSlider::groove:horizontal {{
        background: {c["PANEL_BORDER"]};
        border-radius: 4px;
        height: 7px;
    }}
    QSlider::handle:horizontal {{
        background: {c["ACCENT"]};
        border-radius: 7px;
        width: 14px;
        margin: -4px 0;
    }}
    """
