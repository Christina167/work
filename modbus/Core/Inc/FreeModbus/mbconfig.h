#ifndef _MB_CONFIG_H
#define _MB_CONFIG_H

/* 只启用 Modbus RTU */
#define MB_ASCII_ENABLED                        (0)
#define MB_RTU_ENABLED                          (1)
#define MB_TCP_ENABLED                          (0)

/*
 * 虽然 ASCII 已关闭，但 FreeModbus 公共代码仍会引用这些宏，
 * 因此必须保留定义。
 */
#define MB_ASCII_TIMEOUT_SEC                    (1)

#ifndef MB_ASCII_TIMEOUT_WAIT_BEFORE_SEND_MS
#define MB_ASCII_TIMEOUT_WAIT_BEFORE_SEND_MS    (0)
#endif

/* 功能码处理表容量 */
#define MB_FUNC_HANDLERS_MAX                    (16)

/* Report Slave ID */
#define MB_FUNC_OTHER_REP_SLAVEID_BUF           (32)
#define MB_FUNC_OTHER_REP_SLAVEID_ENABLED       (0)

/* Input Register */
#define MB_FUNC_READ_INPUT_ENABLED              (1)

/* Holding Register：阶段 1 需要的功能 */
#define MB_FUNC_READ_HOLDING_ENABLED            (1)  /* 03 */
#define MB_FUNC_WRITE_HOLDING_ENABLED           (1)  /* 06 */
#define MB_FUNC_WRITE_MULTIPLE_HOLDING_ENABLED  (1)  /* 16 */
#define MB_FUNC_READWRITE_HOLDING_ENABLED       (0)

/* Coil */
#define MB_FUNC_READ_COILS_ENABLED              (0)
#define MB_FUNC_WRITE_COIL_ENABLED              (0)
#define MB_FUNC_WRITE_MULTIPLE_COILS_ENABLED    (0)

/* Discrete Input */
#define MB_FUNC_READ_DISCRETE_INPUTS_ENABLED    (0)

#endif
